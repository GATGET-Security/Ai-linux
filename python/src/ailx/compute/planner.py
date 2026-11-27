# SPDX-License-Identifier: MIT
"""Placement planner: capabilities x model analysis x request -> engine, placement, memory, bottlenecks.

hardware-abstraction.md "Placement planner": feasibility (memory fit per device), lossless first
(lossy KV-cache types only with explicit opt-in), engine/backend selection from *usable* backends,
predicted resource use, and a hand-over to the tuner.

Division of labour with llama.cpp (pinned 98cc730): the engine fits layers to free device memory
itself at load time (``--fit on``, 1024 MiB margin, ``-ngl auto``). This planner does not replace
that; it predicts the result so that admission control can reason across *several* processes, and it
produces explicit configurations for tuning candidates. Memory overheads below are uncalibrated
estimates (hypothesis H-R-11) and are labeled as such in every plan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ailx.compute.model import KV_TYPE_BYTES, LOSSLESS_KV
from ailx.core import Doc

MiB = 1024**2
# Uncalibrated constants (PK / engine defaults). Calibration: compare with llama.cpp buffer logs (H-R-11).
OVERHEAD = {
    "gpu_context_bytes": 400 * MiB,  # CUDA/Vulkan context + cuBLAS workspace (PK, uncalibrated)
    "host_runtime_bytes": 128 * MiB,  # process, tokenizer, server buffers (PK, uncalibrated)
    "fit_margin_bytes": 1024 * MiB,  # llama.cpp --fit-target default at 98cc730 (engine help text)
}
ESTIMATE_LABEL = "uncalibrated estimate (H-R-11): weights/KV exact from the file, buffers heuristic"


@dataclass
class Request:
    ctx: int | None = None  # total context (llama-server -c); None: min(4096, n_ctx_train)
    parallel: int = 1
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"
    allow_lossy: bool = False
    batch: int = 2048
    ubatch: int = 512
    flash_attn: str = "auto"
    device: str = "auto"  # auto | cpu | <accelerator id>
    reserve_ram_bytes: int = 2048 * MiB
    reserve_vram_bytes: int = 0
    use_live: bool = True  # also require the plan to fit currently free memory
    extra: dict[str, Any] = field(default_factory=dict)


def default_ctx(analysis: Doc) -> int:
    train = analysis["hparams"]["n_ctx_train"] or 4096
    return min(4096, train)


def _kv_layer_bytes(analysis: Doc, req: Request) -> list[float]:
    hp = analysis["hparams"]
    k_share = hp["key_length"] / max(1, hp["key_length"] + hp["value_length"])
    per_elem = k_share * KV_TYPE_BYTES[req.cache_type_k] + (1 - k_share) * KV_TYPE_BYTES[req.cache_type_v]
    return [e * per_elem for e in analysis["kv_elements_per_token_per_layer"]]


def _compute_bytes(analysis: Doc, req: Request, ctx: int, flash: bool) -> int:
    """Activation/compute buffers (heuristic): logits + hidden scratch, plus KQ if no flash attention."""
    hp = analysis["hparams"]
    vocab, embd = hp["n_vocab"] or 32000, hp["n_embd"] or 4096
    b = 4 * req.ubatch * (vocab + 4 * embd)
    if not flash:
        b += 4 * req.ubatch * ctx * max(1, hp["n_head"])
    return int(b)


@dataclass
class Split:
    gpu_layers: int  # repeating layers on the GPU (the last ``gpu_layers`` blocks)
    cpu_moe: int  # experts of the first ``cpu_moe`` layers kept in host memory
    output_on_gpu: bool


def memory_for(analysis: Doc, req: Request, ctx: int, split: Split, gpu: bool, flash: bool) -> Doc:
    w = analysis["weights"]
    n = analysis["hparams"]["n_layer"]
    kv = _kv_layer_bytes(analysis, req)
    first_gpu = n - split.gpu_layers
    dev = host = 0.0
    dev_read = host_read = 0.0
    used, n_exp = analysis["hparams"]["n_expert_used"], analysis["hparams"]["n_expert"]
    frac = used / n_exp if n_exp else 1.0
    for i in range(n):
        lw, ew = w["per_layer_bytes"][i], w["per_layer_expert_bytes"][i]
        dense = lw - ew
        on_gpu = gpu and i >= first_gpu
        exp_on_host = (not on_gpu) or i < split.cpu_moe
        if on_gpu:
            dev += dense + kv[i] * ctx
            dev_read += dense + kv[i] * ctx
        else:
            host += dense + kv[i] * ctx
            host_read += dense + kv[i] * ctx
        if exp_on_host:
            host += ew
            host_read += ew * frac
        else:
            dev += ew
            dev_read += ew * frac
    if gpu and split.output_on_gpu:
        dev += w["output_bytes"]
        dev_read += w["output_bytes"]
    else:
        host += w["output_bytes"]
        host_read += w["output_bytes"]
    host += w["embedding_bytes"] + OVERHEAD["host_runtime_bytes"]
    comp = _compute_bytes(analysis, req, ctx, flash)
    if gpu and (split.gpu_layers or split.output_on_gpu):
        dev += comp + OVERHEAD["gpu_context_bytes"]
        host += 4 * req.ubatch * (analysis["hparams"]["n_embd"] or 4096)  # host-side staging (heuristic)
    else:
        host += comp
    return {
        "device_bytes": int(dev),
        "host_bytes": int(host),
        "device_read_per_token_bytes": int(dev_read),
        "host_read_per_token_bytes": int(host_read),
        "kv_bytes": int(sum(kv) * ctx),
    }


def _fits(mem: Doc, gpu_cap: int | None, ram_cap: int) -> bool:
    ok_dev = gpu_cap is None or mem["device_bytes"] + OVERHEAD["fit_margin_bytes"] <= gpu_cap
    return bool(ok_dev and mem["host_bytes"] <= ram_cap)


def _choose_split(analysis: Doc, req: Request, ctx: int, gpu_cap: int, ram_cap: int, flash: bool) -> Split | None:
    n = analysis["hparams"]["n_layer"]
    full = Split(n, 0, True)
    if _fits(memory_for(analysis, req, ctx, full, True, flash), gpu_cap, ram_cap):
        return full
    if analysis["hparams"]["n_expert"]:  # MoE: keep attention on the GPU, move experts of the first m layers
        for m in range(1, n + 1):
            s = Split(n, m, True)
            if _fits(memory_for(analysis, req, ctx, s, True, flash), gpu_cap, ram_cap):
                return s
    for k in range(n - 1, 0, -1):  # dense: offload the last k layers
        s = Split(k, 0, False)
        if _fits(memory_for(analysis, req, ctx, s, True, flash), gpu_cap, ram_cap):
            return s
    return None


def _pick_gpu(caps: Doc, req: Request) -> Doc | None:
    gpus = [
        d
        for d in caps["accelerators"]
        if d["kind"] == "gpu" and d["memory"]["kind"] == "discrete" and d["memory"]["total_bytes"]
    ]
    if req.device == "cpu":
        return None
    if req.device != "auto":
        return next((d for d in gpus if d["id"] == req.device), None)
    usable = {dev for b in caps["backends"] if b["usable"] and b["backend"] != "cpu" for dev in b["devices"]}
    ranked = sorted(gpus, key=lambda d: (d["id"] in usable, d["memory"]["total_bytes"]), reverse=True)
    return ranked[0] if ranked else None


def _backend_for(caps: Doc, gpu: Doc | None) -> tuple[str, list[str]]:
    actions = []
    if gpu is None:
        be = "cpu"
    else:
        order = [b for b in ("cuda", "rocm", "vulkan", "sycl") if b in gpu["backends"]]
        if not order:
            be = "cpu"
            actions.append(f"{gpu['id']}: no GPU compute stack usable (driver userspace missing); see 'ailx doctor'")
        else:
            be = order[0]
    by = {b["backend"]: b for b in caps["backends"]}
    if not by.get(be, {}).get("engine_builds"):
        variant = {"cuda": "cuda", "vulkan": "vulkan", "cpu": "cpu"}.get(be, be)
        actions.append(f"no llama.cpp build for {be}: ailx engine install llama.cpp --variant {variant}")
    return be, actions


def max_ctx(analysis: Doc, req: Request, gpu_cap: int | None, ram_cap: int, split_full_gpu: bool, flash: bool) -> int:
    """Largest context (multiple of 256) that keeps the chosen placement feasible."""
    n = analysis["hparams"]["n_layer"]
    split = Split(n, 0, True) if split_full_gpu else Split(0, 0, False)
    lo, hi = 0, max(256, analysis["hparams"]["n_ctx_train"] or 4096)
    while lo < hi:
        mid = (lo + hi + 256) // 512 * 256 or 256
        if mid > hi:
            break
        if _fits(memory_for(analysis, req, mid, split, gpu_cap is not None, flash), gpu_cap, ram_cap):
            lo = mid
        else:
            hi = mid - 256
    return lo


def plan(caps: Doc, analysis: Doc, req: Request | None = None) -> Doc:
    req = req or Request()
    notes: list[str] = list(analysis["notes"])
    for t in (req.cache_type_k, req.cache_type_v):
        if t not in KV_TYPE_BYTES:
            raise ValueError(f"unknown KV cache type {t!r}")
        if t not in LOSSLESS_KV and not req.allow_lossy:
            raise ValueError(f"KV cache type {t!r} is lossy: requires explicit opt-in (allow_lossy)")
    ctx = req.ctx or default_ctx(analysis)
    if analysis["hparams"]["n_ctx_train"] and ctx > analysis["hparams"]["n_ctx_train"]:
        notes.append(f"requested ctx {ctx} exceeds the trained context {analysis['hparams']['n_ctx_train']}")
    total_ram = caps["memory"]["total_bytes"] or 0
    ram_cap = max(0, total_ram - req.reserve_ram_bytes)
    live_ram = caps["live"].get("memory_available_bytes")
    if req.use_live and live_ram is not None:
        ram_cap = min(ram_cap, max(0, live_ram - req.reserve_ram_bytes // 2))
    gpu = _pick_gpu(caps, req)
    backend, actions = _backend_for(caps, gpu)
    gpu_cap = None
    if gpu is not None and backend != "cpu":
        gpu_cap = gpu["memory"]["total_bytes"] - req.reserve_vram_bytes
        live = caps["live"].get(gpu["id"], {}).get("vram_free_bytes")
        if req.use_live and live is not None:
            gpu_cap = min(gpu_cap, live)
    flash = req.flash_attn == "on" or (req.flash_attn == "auto" and backend in ("cuda", "vulkan", "rocm"))
    split: Split | None
    if gpu_cap is not None:
        split = _choose_split(analysis, req, ctx, gpu_cap, ram_cap, flash)
        if split is None:  # nothing on the GPU fits either: try plain CPU
            cpu_mem = memory_for(analysis, req, ctx, Split(0, 0, False), False, flash)
            split = Split(0, 0, False) if cpu_mem["host_bytes"] <= ram_cap else None
    else:
        cpu_mem = memory_for(analysis, req, ctx, Split(0, 0, False), False, flash)
        split = Split(0, 0, False) if cpu_mem["host_bytes"] <= ram_cap else None
    n = analysis["hparams"]["n_layer"]
    on_gpu = split is not None and gpu_cap is not None and (split.gpu_layers > 0 or split.output_on_gpu)
    if split is None:
        mode = "infeasible"
        mem = memory_for(analysis, req, ctx, Split(0, 0, False), False, flash)
    else:
        mode = (
            "cpu"
            if not on_gpu
            else "gpu-full"
            if split.gpu_layers == n and split.cpu_moe == 0
            else "hybrid-moe"
            if split.cpu_moe
            else "hybrid-layers"
        )
        mem = memory_for(analysis, req, ctx, split, on_gpu, flash)
    if not on_gpu and backend != "cpu":
        backend, more = _backend_for(caps, None)
        actions += more
    engine_cfg: Doc = {
        "ctx_size": ctx,
        "parallel": req.parallel,
        "n_gpu_layers": (n + 1 if split.output_on_gpu else split.gpu_layers) if split and on_gpu else 0,
        "n_cpu_moe": split.cpu_moe if split else 0,
        "batch_size": req.batch,
        "ubatch_size": req.ubatch,
        "flash_attn": req.flash_attn,
        "cache_type_k": req.cache_type_k,
        "cache_type_v": req.cache_type_v,
    }
    lossy = [t for t in (req.cache_type_k, req.cache_type_v) if t not in LOSSLESS_KV]
    result: Doc = {
        "model_id": analysis["id"],
        "capabilities_id": caps["id"],
        "mode": mode,
        "feasible": mode != "infeasible",
        "engine": "llama.cpp" if analysis["format"] == "gguf" else "vllm",
        "backend": backend if mode != "infeasible" else None,
        "device": gpu["id"] if on_gpu and gpu else "cpu",
        "engine_config": engine_cfg,
        "memory": mem,
        "budget": {"ram_bytes": ram_cap, "device_bytes": gpu_cap, "fit_margin_bytes": OVERHEAD["fit_margin_bytes"]},
        "max_ctx": {
            "gpu_full": max_ctx(analysis, req, gpu_cap, ram_cap, True, flash) if gpu_cap is not None else None,
            "cpu": max_ctx(analysis, req, None, ram_cap, False, flash),
        },
        "quality": {"lossy_settings": lossy, "label": "lossy (opt-in)" if lossy else "lossless"},
        "bottlenecks": bottlenecks(caps, analysis, mem, gpu if on_gpu else None, mode, live_ram),
        "actions": actions,
        "estimate": ESTIMATE_LABEL,
        "notes": notes,
        "request": asdict(req),
    }
    if analysis["format"] != "gguf":
        result.update(_vllm(analysis, gpu, mem, backend, result))
    if mode == "infeasible":
        result["suggestions"] = _suggestions(analysis, req, result)
    return result


def _vllm(analysis: Doc, gpu: Doc | None, mem: Doc, backend: str, result: Doc) -> Doc:
    """vLLM keeps the whole model in VRAM and preallocates ~90 % of it for weights + KV (PK, engine default)."""
    if gpu is None or backend != "cuda":
        return {"feasible": False, "mode": "infeasible", "notes": [*result["notes"], "vLLM needs a CUDA GPU"]}
    need = analysis["weights"]["total_bytes"] + mem["kv_bytes"]
    cap = int(0.9 * gpu["memory"]["total_bytes"])
    ok = need <= cap
    return {
        "mode": "gpu-full" if ok else "infeasible",
        "feasible": ok,
        "engine_config": {"max_model_len": result["engine_config"]["ctx_size"], "gpu_memory_utilization": 0.9},
        "notes": [*result["notes"], "safetensors model: vLLM (GGUF via llama.cpp is the alternative)"],
    }


def bottlenecks(caps: Doc, analysis: Doc, mem: Doc, gpu: Doc | None, mode: str, live_ram: int | None) -> Doc:
    """Theoretical decode bound and qualitative bottleneck classes (never a performance claim)."""
    out: Doc = {"classes": [], "decode_upper_bound_tokens_per_s": None, "formula": None}
    if mode == "infeasible":
        out["classes"].append("capacity: does not fit the memory budget")
        return out
    gbw = gpu["memory"]["bandwidth"]["bytes_per_s"] if gpu else None
    cbw = caps["memory"]["bandwidth"]["bytes_per_s"]
    d, h = mem["device_read_per_token_bytes"], mem["host_read_per_token_bytes"]
    t = 0.0
    known = True
    if d:
        if gbw:
            t += d / gbw
        else:
            known = False
    if h:
        if cbw:
            t += h / cbw
        else:
            known = False
    if known and t > 0:
        out["decode_upper_bound_tokens_per_s"] = round(1 / t, 2)
        out["formula"] = "theoretical: 1 / (device_bytes_read/device_bw + host_bytes_read/host_bw), full context"
    else:
        out["formula"] = "not computed: memory bandwidth of a used memory domain is unknown"
    if d and not h:
        out["classes"].append("decode: device memory bandwidth")
    elif h and d:
        out["classes"].append("decode: host memory bandwidth for CPU-resident weights (hybrid)")
    else:
        out["classes"].append("decode: host memory bandwidth (CPU)")
        out["classes"].append("prefill: CPU compute (qualitative)")
    if live_ram is not None and mem["host_bytes"] > live_ram:
        out["classes"].append("io: host part exceeds available RAM, weights would page from storage (mmap)")
    size = analysis["file_bytes"]
    out["load"] = {"bytes": size, "note": "cold load reads the whole file once (H-S-06); storage throughput unmeasured"}
    return out


def _suggestions(analysis: Doc, req: Request, result: Doc) -> list[str]:
    s = []
    mc = result["max_ctx"]
    best = max(c for c in (mc["gpu_full"], mc["cpu"]) if c is not None)
    if best >= 256:
        s.append(f"reduce context to <= {best} tokens (lossless)")
    if req.cache_type_k == "f16":
        s.append("quantize the KV cache (-ctk/-ctv q8_0): lossy, requires --allow-lossy and a quality-gate run")
    s.append("use a smaller quantization of the model (lossy; separate file) or a smaller model")
    return s
