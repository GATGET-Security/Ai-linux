# SPDX-License-Identifier: MIT
"""Quality gate v0: KL divergence against stored reference logits (llama.cpp ``llama-perplexity``) [S-62].

Comparison prerequisites are checked and recorded — a KLD number is only meaningful if:

1. the reference logits file is the one recorded at creation (sha256),
2. the evaluation corpus is byte-identical (sha256),
3. reference and candidate share the same vocabulary (sha256 of the GGUF token list),
4. context size and chunk count are identical,
5. the same llama.cpp build is used (otherwise kernel differences mix into the result).

Gate rule (initial policy): the candidate passes if its mean KLD and same-top-p are statistically
indistinguishable from the reference-vs-itself (A/A) run at ±2 standard errors. A pass means
"no detectable quality change on this corpus"; it is **not** proof of equivalence.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ailx.core import Doc, file_sha256, sha256_hex, utcnow

FLOAT = r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
PATTERNS = {
    "kld_mean": re.compile(rf"Mean\s+KLD:\s+{FLOAT}\s+±\s+{FLOAT}"),
    "same_top_p": re.compile(rf"Same top p:\s+{FLOAT}\s+±\s+{FLOAT}\s*%"),
    "ppl_q": re.compile(rf"Mean PPL\(Q\)\s+:\s+{FLOAT}\s+±\s+{FLOAT}"),
    "ppl_base": re.compile(rf"Mean PPL\(base\)\s+:\s+{FLOAT}\s+±\s+{FLOAT}"),
    "kld_p99": re.compile(rf"99\.0%\s+KLD:\s+{FLOAT}"),
    "kld_max": re.compile(rf"Maximum KLD:\s+{FLOAT}"),
    "rms_dp": re.compile(rf"RMS Δp\s+:\s+{FLOAT}\s+±\s+{FLOAT}\s*%"),
}


def parse_kld_output(text: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for key, pat in PATTERNS.items():
        m = pat.search(text)
        if m:
            vals = [float(x) for x in m.groups()]
            out[key] = {"value": vals[0], **({"se": vals[1]} if len(vals) > 1 else {})}
    return out


def vocab_sha256(model: str | Path) -> str | None:
    try:
        from gguf import GGUFReader
    except ImportError:
        return None
    reader = GGUFReader(str(model))
    field = reader.fields.get("tokenizer.ggml.tokens")
    if field is None:
        return None
    tokens = b"\x00".join(bytes(field.parts[i]) for i in field.data)
    return sha256_hex(tokens)


def llama_cpp_build(binary: str) -> str | None:
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except OSError:
        return None
    m = re.search(r"version:\s*(\S+)\s*\(build\s*(\d+),\s*commit\s*(\w+)\)", r.stdout + r.stderr)
    return f"{m.group(3)} (build {m.group(2)}, {m.group(1)})" if m else None


def _run(binary: str, args: list[str], timeout: float) -> str:
    r = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"llama-perplexity failed (exit {r.returncode}): {r.stderr[-2000:]}")
    return r.stdout + r.stderr


def make_reference(
    binary: str,
    model: str,
    corpus: str,
    out_dir: str | Path,
    ctx: int = 512,
    chunks: int = 32,
    threads: int | None = None,
    timeout: float = 7200,
) -> Doc:
    """Create reference logits and the A/A floor (reference evaluated against itself)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    kld = out / "reference.kld"
    common = ["-f", corpus, "-c", str(ctx), "--chunks", str(chunks)] + (["-t", str(threads)] if threads else [])
    _run(binary, ["-m", model, "--kl-divergence-base", str(kld), *common], timeout)
    aa_text = _run(binary, ["-m", model, "--kl-divergence-base", str(kld), "--kl-divergence", *common], timeout)
    meta: Doc = {
        "kind": "ailx.kld-reference/v0",
        "created_at": utcnow(),
        "reference_model": {"path": model, "sha256": file_sha256(model), "vocab_sha256": vocab_sha256(model)},
        "corpus": {"path": corpus, "sha256": file_sha256(corpus)},
        "ctx": ctx,
        "chunks": chunks,
        "threads": threads,
        "llama_cpp_build": llama_cpp_build(binary),
        "kld_file": {"path": str(kld), "sha256": file_sha256(kld)},
        "aa": parse_kld_output(aa_text),
    }
    (out / "reference.json").write_text(json.dumps(meta, indent=2))
    return meta


def check_prerequisites(meta: Doc, binary: str, candidate: str, corpus: str | None = None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    kld = Path(meta["kld_file"]["path"])
    add(
        "reference-logits-integrity",
        kld.is_file() and file_sha256(kld) == meta["kld_file"]["sha256"],
        "sha256 of reference.kld matches the recorded value",
    )
    corpus_path = corpus or meta["corpus"]["path"]
    add(
        "corpus-identical",
        Path(corpus_path).is_file() and file_sha256(corpus_path) == meta["corpus"]["sha256"],
        "evaluation corpus is byte-identical to the reference corpus",
    )
    ref_vocab, cand_vocab = meta["reference_model"].get("vocab_sha256"), vocab_sha256(candidate)
    if ref_vocab is None or cand_vocab is None:
        add("vocabulary-identical", None, "could not read GGUF vocabularies (install the 'gguf' extra)")
    else:
        add("vocabulary-identical", ref_vocab == cand_vocab, "GGUF token lists are identical")
    build = llama_cpp_build(binary)
    add(
        "same-llama-cpp-build",
        build is not None and build == meta.get("llama_cpp_build"),
        f"reference build {meta.get('llama_cpp_build')}, current build {build}",
    )
    add(
        "aa-floor-present",
        "kld_mean" in meta.get("aa", {}) and "same_top_p" in meta.get("aa", {}),
        "A/A floor recorded at reference creation",
    )
    return checks


def gate(candidate: dict[str, dict[str, float]], aa: dict[str, dict[str, float]], k: float = 2.0) -> tuple[str, str]:
    c_kld, a_kld = candidate["kld_mean"], aa["kld_mean"]
    c_top, a_top = candidate["same_top_p"], aa["same_top_p"]
    kld_ok = c_kld["value"] - k * c_kld.get("se", 0) <= a_kld["value"] + k * a_kld.get("se", 0)
    top_ok = c_top["value"] + k * c_top.get("se", 0) >= a_top["value"] - k * a_top.get("se", 0)
    if kld_ok and top_ok:
        return "no-detectable-quality-change", "within ±2 SE of the A/A floor on this corpus (not proof of equivalence)"
    return "quality-change-detected", (
        f"KLD {c_kld['value']:.6g} vs A/A {a_kld['value']:.6g}; "
        f"same-top-p {c_top['value']:.4g}% vs A/A {a_top['value']:.4g}%"
    )


def evaluate(
    binary: str,
    candidate: str,
    reference_json: str | Path,
    threads: int | None = None,
    timeout: float = 7200,
    corpus: str | None = None,
    extra_args: list[str] | None = None,
) -> Doc:
    """``extra_args`` are passed to the candidate run only (e.g. ``-ctk q8_0``: lossy KV-cache types)."""
    meta: Doc = json.loads(Path(reference_json).read_text())
    checks = check_prerequisites(meta, binary, candidate, corpus)
    hard_fail = [c for c in checks if c["ok"] is False]
    unknown = [c for c in checks if c["ok"] is None]
    out: Doc = {
        "reference": str(reference_json),
        "candidate": {"path": candidate, "sha256": file_sha256(candidate), "extra_args": list(extra_args or [])},
        "prerequisites": checks,
        "metrics": {},
        "verdict": None,
        "detail": None,
    }
    if hard_fail:
        out["verdict"], out["detail"] = "prerequisite-failed", "; ".join(c["check"] for c in hard_fail)
        return out
    common = ["-f", corpus or meta["corpus"]["path"], "-c", str(meta["ctx"]), "--chunks", str(meta["chunks"])]
    if threads:
        common += ["-t", str(threads)]
    text = _run(
        binary,
        [
            "-m",
            candidate,
            "--kl-divergence-base",
            meta["kld_file"]["path"],
            "--kl-divergence",
            *common,
            *(extra_args or []),
        ],
        timeout,
    )
    metrics = parse_kld_output(text)
    out["metrics"] = metrics
    if "kld_mean" not in metrics or "same_top_p" not in metrics:
        out["verdict"], out["detail"] = "invalid", "could not parse KLD statistics"
    elif unknown:
        out["verdict"] = "prerequisite-unverified"
        out["detail"] = "; ".join(c["check"] for c in unknown) + " not verifiable; gate not applied"
    else:
        out["verdict"], out["detail"] = gate(metrics, meta["aa"])
    return out
