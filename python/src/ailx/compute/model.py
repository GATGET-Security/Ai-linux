# SPDX-License-Identifier: MIT
"""Model analysis: what a model needs (weights per layer, experts, KV cache per token).

Facts come from the file (GGUF header, or ``config.json`` + safetensors headers for Hugging Face
directories). Derived numbers are formulas over those facts; where the formula is known to be an
upper bound (sliding-window or recurrent layers) the analysis says so instead of pretending precision.
The document ID hashes the analysis, not the path, so the same file analysed anywhere has one ID.
"""

from __future__ import annotations

import json
import re
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any

from ailx.compute.gguf import read_gguf
from ailx.core import Doc, canonical_bytes, sha256_hex, utcnow, validate

# KV-cache element sizes in bytes for llama.cpp cache types (-ctk/-ctv); q* are lossy (opt-in only)
KV_TYPE_BYTES = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34 / 32,
    "q5_1": 24 / 32,
    "q5_0": 22 / 32,
    "q4_1": 20 / 32,
    "q4_0": 18 / 32,
    "iq4_nl": 18 / 32,
}
LOSSLESS_KV = {"f32", "f16", "bf16"}
RECURRENT_ARCHS = {"mamba", "mamba2", "rwkv6", "rwkv7", "jamba", "granitehybrid", "falcon-h1", "plamo2", "lfm2"}
_LAYER = re.compile(r"^blk\.(\d+)\.")


def _per_layer(value: Any, n_layer: int) -> list[int]:
    if isinstance(value, list):
        return [int(v) for v in value][:n_layer] + [int(value[-1])] * max(0, n_layer - len(value))
    return [int(value)] * n_layer


def _dominant(bytes_by_type: dict[str, int]) -> str | None:
    return max(bytes_by_type, key=lambda k: bytes_by_type[k]) if bytes_by_type else None


def analyze_gguf(path: Path) -> Doc:
    g = read_gguf(path)
    meta, tensors = g["metadata"], g["tensors"]
    arch = str(meta.get("general.architecture", "unknown"))

    def m(key: str, default: Any = None) -> Any:
        return meta.get(f"{arch}.{key}", default)

    n_layer = int(m("block_count", 0))
    n_embd = int(m("embedding_length", 0))
    heads = _per_layer(m("attention.head_count", 0), n_layer)
    heads_kv = _per_layer(m("attention.head_count_kv", m("attention.head_count", 0)), n_layer)
    head_dim = (n_embd // heads[0]) if heads and heads[0] else 0
    k_len = int(m("attention.key_length", head_dim))
    v_len = int(m("attention.value_length", head_dim))
    n_expert, n_expert_used = int(m("expert_count", 0)), int(m("expert_used_count", 0))
    vocab = meta.get("tokenizer.ggml.tokens")
    n_vocab = vocab["length"] if isinstance(vocab, dict) else (len(vocab) if isinstance(vocab, list) else None)
    if n_vocab is None:
        emb = next((t for t in tensors if t["name"] == "token_embd.weight"), None)
        n_vocab = emb["shape"][1] if emb and len(emb["shape"]) > 1 else None

    layer_bytes = [0] * n_layer
    layer_expert_bytes = [0] * n_layer
    by_type: dict[str, int] = defaultdict(int)
    params = 0
    other = embedding = 0
    for t in tensors:
        by_type[t["type"]] += t["bytes"]
        n = 1
        for d in t["shape"]:
            n *= d
        params += n
        lm = _LAYER.match(t["name"])
        if lm and int(lm.group(1)) < n_layer:
            i = int(lm.group(1))
            layer_bytes[i] += t["bytes"]
            if "_exps" in t["name"]:
                layer_expert_bytes[i] += t["bytes"]
        elif t["name"].startswith("token_embd"):
            embedding += t["bytes"]  # input embeddings stay in host memory in llama.cpp
        else:
            other += t["bytes"]
    total = sum(by_type.values())
    kv_elems = [heads_kv[i] * (k_len + v_len) for i in range(n_layer)]
    swa = m("attention.sliding_window")
    notes = []
    if swa:
        notes.append(f"sliding-window attention ({swa}): KV estimate is an upper bound")
    if arch in RECURRENT_ARCHS:
        notes.append(f"{arch} has recurrent/state layers: KV estimate does not model them")
    expert_total = sum(layer_expert_bytes)
    active = total - expert_total + (expert_total * n_expert_used // n_expert if n_expert else 0)
    return {
        "format": "gguf",
        "architecture": arch,
        "name": meta.get("general.name"),
        "gguf_version": g["version"],
        "file_bytes": g["file_bytes"],
        "params": params,
        "weights": {
            "total_bytes": total,
            "bits_per_weight": round(total * 8 / params, 3) if params else None,
            "by_type_bytes": dict(sorted(by_type.items())),
            "dominant_type": _dominant(by_type),
            "file_type": meta.get("general.file_type"),
            "per_layer_bytes": layer_bytes,
            "per_layer_expert_bytes": layer_expert_bytes,
            "embedding_bytes": embedding,
            "output_bytes": other,
            "active_bytes_per_token": active,
        },
        "hparams": {
            "n_layer": n_layer,
            "n_embd": n_embd,
            "n_head": heads[0] if heads else 0,
            "n_head_kv": heads_kv,
            "key_length": k_len,
            "value_length": v_len,
            "n_ctx_train": int(m("context_length", 0)),
            "n_vocab": n_vocab,
            "n_expert": n_expert,
            "n_expert_used": n_expert_used,
            "sliding_window": swa,
        },
        "kv_elements_per_token_per_layer": kv_elems,
        "notes": notes,
    }


# ---------------------------------------------------------------- Hugging Face / safetensors

_ST_DTYPE = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "F8_E4M3": 1, "F8_E5M2": 1, "I64": 8, "I32": 4, "I16": 2,
             "I8": 1, "U8": 1, "BOOL": 1}  # fmt: skip


def _safetensors_header(p: Path) -> dict[str, Any]:
    with open(p, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 << 20:
            raise ValueError(f"{p.name}: implausible safetensors header size")
        hdr: dict[str, Any] = json.loads(f.read(n))
    return hdr


def analyze_hf(path: Path) -> Doc:
    cfg = json.loads((path / "config.json").read_text())
    tc = cfg.get("text_config", cfg)  # multimodal configs nest the language model
    n_layer = int(tc.get("num_hidden_layers", 0))
    n_embd = int(tc.get("hidden_size", 0))
    n_head = int(tc.get("num_attention_heads", 0))
    n_kv = int(tc.get("num_key_value_heads", n_head))
    head_dim = int(tc.get("head_dim") or (n_embd // n_head if n_head else 0))
    n_expert = int(tc.get("num_local_experts", tc.get("num_experts", 0)) or 0)
    used = int(tc.get("num_experts_per_tok", 0) or 0)
    by_type: dict[str, int] = defaultdict(int)
    layer_bytes = [0] * n_layer
    layer_expert = [0] * n_layer
    params = other = embedding = 0
    files = sorted(path.glob("*.safetensors"))
    for f in files:
        for name, t in _safetensors_header(f).items():
            if name == "__metadata__":
                continue
            n = 1
            for d in t["shape"]:
                n *= d
            b = n * _ST_DTYPE.get(t["dtype"], 2)
            by_type[t["dtype"]] += b
            params += n
            lm = re.search(r"layers\.(\d+)\.", name)
            if lm and int(lm.group(1)) < n_layer:
                i = int(lm.group(1))
                layer_bytes[i] += b
                if ".experts." in name:
                    layer_expert[i] += b
            elif "embed_tokens" in name:
                embedding += b
            else:
                other += b
    total = sum(by_type.values())
    expert_total = sum(layer_expert)
    notes = [] if files else ["no *.safetensors files: weight sizes unknown"]
    if tc.get("sliding_window"):
        notes.append(f"sliding-window attention ({tc['sliding_window']}): KV estimate is an upper bound")
    return {
        "format": "safetensors",
        "architecture": (cfg.get("architectures") or ["unknown"])[0],
        "name": cfg.get("_name_or_path") or path.name,
        "gguf_version": None,
        "file_bytes": sum(f.stat().st_size for f in files),
        "params": params,
        "weights": {
            "total_bytes": total,
            "bits_per_weight": round(total * 8 / params, 3) if params else None,
            "by_type_bytes": dict(sorted(by_type.items())),
            "dominant_type": _dominant(by_type),
            "file_type": None,
            "per_layer_bytes": layer_bytes,
            "per_layer_expert_bytes": layer_expert,
            "embedding_bytes": embedding,
            "output_bytes": other,
            "active_bytes_per_token": total - expert_total + (expert_total * used // n_expert if n_expert else 0),
        },
        "hparams": {
            "n_layer": n_layer,
            "n_embd": n_embd,
            "n_head": n_head,
            "n_head_kv": [n_kv] * n_layer,
            "key_length": head_dim,
            "value_length": head_dim,
            "n_ctx_train": int(tc.get("max_position_embeddings", 0) or 0),
            "n_vocab": tc.get("vocab_size"),
            "n_expert": n_expert,
            "n_expert_used": used,
            "sliding_window": tc.get("sliding_window"),
        },
        "kv_elements_per_token_per_layer": [n_kv * 2 * head_dim] * n_layer,
        "notes": notes,
    }


def analyze(path: str | Path) -> Doc:
    p = Path(path)
    if p.is_dir() and (p / "config.json").is_file():
        body = analyze_hf(p)
    elif p.is_file():
        body = analyze_gguf(p)
    else:
        raise FileNotFoundError(f"{path}: not a GGUF file or a Hugging Face model directory")
    doc: Doc = {"schema": "ailx.model-analysis/v0", "analyzed_at": utcnow(), "source": {"path": str(p)}, **body}
    return seal_analysis(doc)


def seal_analysis(doc: Doc) -> Doc:
    body = {k: v for k, v in doc.items() if k not in ("analyzed_at", "id", "source")}
    out = dict(doc)
    out["id"] = "mdl-" + sha256_hex(canonical_bytes(body))
    validate(out)
    return out


def kv_bytes_per_token(analysis: Doc, cache_k: str = "f16", cache_v: str = "f16", layers: range | None = None) -> float:
    """KV cache bytes per context token (all layers, or ``layers``); K and V may use different types."""
    hp = analysis["hparams"]
    idx = layers if layers is not None else range(hp["n_layer"])
    k_share = hp["key_length"] / max(1, hp["key_length"] + hp["value_length"])
    per = analysis["kv_elements_per_token_per_layer"]
    return float(sum(per[i] * (k_share * KV_TYPE_BYTES[cache_k] + (1 - k_share) * KV_TYPE_BYTES[cache_v]) for i in idx))
