# SPDX-License-Identifier: MIT
"""Synthetic model analyses and capability docs for planner/runtime/tuning tests.

Shapes are modeled on public architectures (Llama-3-8B/70B-like dense, gpt-oss-120b-like MoE); byte
counts are derived from those shapes and a bits-per-weight figure, not read from real files.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ailx.compute.capabilities import collect_capabilities
from ailx.compute.model import seal_analysis
from ailx.system.paths import Layout

GiB = 1024**3


def synthetic_analysis(
    name: str,
    n_layer: int,
    n_embd: int,
    n_head: int,
    n_head_kv: int,
    head_dim: int,
    vocab: int,
    params_b: float,
    bpw: float,
    ctx_train: int = 131072,
    n_expert: int = 0,
    n_expert_used: int = 0,
    expert_share: float = 0.0,
) -> dict[str, Any]:
    total = int(params_b * 1e9 * bpw / 8)
    emb = int(vocab * n_embd * bpw / 8)
    out = emb
    per_layer = (total - emb - out) // n_layer
    experts = int(per_layer * expert_share)
    active = total - experts * n_layer + (experts * n_layer * n_expert_used // n_expert if n_expert else 0)
    doc = {
        "schema": "ailx.model-analysis/v0",
        "analyzed_at": "2026-11-27T00:00:00Z",
        "source": {"path": f"/models/{name}.gguf"},
        "format": "gguf",
        "architecture": "llama" if not n_expert else "gpt-oss",
        "name": name,
        "gguf_version": 3,
        "file_bytes": total + 8 * 1024 * 1024,
        "params": int(params_b * 1e9),
        "weights": {
            "total_bytes": total,
            "bits_per_weight": bpw,
            "by_type_bytes": {"Q4_K": total},
            "dominant_type": "Q4_K",
            "file_type": 15,
            "per_layer_bytes": [per_layer] * n_layer,
            "per_layer_expert_bytes": [experts] * n_layer,
            "embedding_bytes": emb,
            "output_bytes": out,
            "active_bytes_per_token": active,
        },
        "hparams": {
            "n_layer": n_layer,
            "n_embd": n_embd,
            "n_head": n_head,
            "n_head_kv": [n_head_kv] * n_layer,
            "key_length": head_dim,
            "value_length": head_dim,
            "n_ctx_train": ctx_train,
            "n_vocab": vocab,
            "n_expert": n_expert,
            "n_expert_used": n_expert_used,
            "sliding_window": None,
        },
        "kv_elements_per_token_per_layer": [n_head_kv * 2 * head_dim] * n_layer,
        "notes": [],
    }
    return seal_analysis(doc)


def dense_8b() -> dict[str, Any]:
    return synthetic_analysis("dense-8b-q4", 32, 4096, 32, 8, 128, 128256, 8.0, 4.85)


def dense_70b() -> dict[str, Any]:
    return synthetic_analysis("dense-70b-q4", 80, 8192, 64, 8, 128, 128256, 70.6, 4.85)


def moe_120b() -> dict[str, Any]:
    return synthetic_analysis(
        "moe-120b-mxfp4", 36, 2880, 64, 8, 64, 201088, 117.0, 4.25, n_expert=128, n_expert_used=4, expert_share=0.95
    )


def layout_for(root: Path) -> Layout:
    return Layout(
        root=root,
        state_dir=root / "var/lib/ailx",
        engines_dir=root / "var/lib/ailx/engines",
        helper="/nonexistent",
        mode="user",
        models_dir=Path("/var/lib/ailx/models"),
    )


def workstation_caps(desktop_root: Path, cuda: bool = True, vram_free: int | None = None) -> dict[str, Any]:
    """Capabilities of the 7950X3D + RTX 4090 fixture with the GPU as NVML would report it."""
    lay = layout_for(desktop_root)
    if cuda:
        (desktop_root / "usr/lib/x86_64-linux-gnu").mkdir(parents=True, exist_ok=True)
        (desktop_root / "usr/lib/x86_64-linux-gnu/libcuda.so.1").write_text("")
        for variant in ("cuda", "cpu"):
            d = lay.engines_dir / "llama.cpp" / f"98cc730cc8f8-{variant}"
            (d / "bin").mkdir(parents=True, exist_ok=True)
            manifest = {"engine": "llama.cpp", "install_id": d.name, "variant": variant, "kind": "cmake"}
            (d / "manifest.json").write_text(json.dumps(manifest))
    caps = copy.deepcopy(collect_capabilities(lay, use_nvml=False))
    gpu = caps["accelerators"][0]
    gpu["name"] = "NVIDIA GeForce RTX 4090"
    gpu["memory"] = {
        "kind": "discrete",
        "total_bytes": 24564 * 1024**2,
        "bandwidth": {"bytes_per_s": 1008e9, "provenance": "datasheet:H-12", "kind": "theoretical"},
    }
    caps["live"]["nvidia:0"] = {"vram_free_bytes": vram_free if vram_free is not None else 24000 * 1024**2}
    caps["live"]["memory_available_bytes"] = 60 * GiB
    return caps
