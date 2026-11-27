# SPDX-License-Identifier: MIT
"""GGUF reader, model analysis and placement planner."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from compute_fix import dense_8b, dense_70b, moe_120b, workstation_caps

from ailx.compute import gguf as G
from ailx.compute.model import analyze, kv_bytes_per_token
from ailx.compute.planner import Request, plan

gguf = pytest.importorskip("gguf")
GiB = 1024**3


def write_model(path: Path, n_layer: int = 2, embd: int = 64, n_head: int = 4, n_kv: int = 2, moe: int = 0) -> Path:
    w = gguf.GGUFWriter(str(path), "llama")
    w.add_context_length(256)
    w.add_embedding_length(embd)
    w.add_block_count(n_layer)
    w.add_head_count(n_head)
    w.add_head_count_kv(n_kv)
    w.add_feed_forward_length(128)
    if moe:
        w.add_expert_count(moe)
        w.add_expert_used_count(2)
    w.add_token_list([f"t{i}" for i in range(2000)])  # longer than KEEP_ARRAY: summarised, not stored
    rng = np.random.default_rng(0)
    w.add_tensor("token_embd.weight", rng.standard_normal((2000, embd)).astype(np.float16))
    w.add_tensor("output.weight", rng.standard_normal((2000, embd)).astype(np.float16))
    for i in range(n_layer):
        w.add_tensor(f"blk.{i}.attn_q.weight", rng.standard_normal((embd, embd)).astype(np.float16))
        w.add_tensor(f"blk.{i}.attn_norm.weight", rng.standard_normal(embd).astype(np.float32))
        if moe:
            w.add_tensor(f"blk.{i}.ffn_up_exps.weight", rng.standard_normal((moe, 128, embd)).astype(np.float16))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


def test_ggml_type_table_matches_reference() -> None:
    from gguf.constants import GGML_QUANT_SIZES

    ref = {int(t): v for t, v in GGML_QUANT_SIZES.items()}
    for tid, (_, blk, size) in G.GGML_TYPES.items():
        assert ref[tid] == (blk, size), tid


def test_reader_and_analysis(tmp_path: Path) -> None:
    a = analyze(write_model(tmp_path / "m.gguf"))
    w, hp = a["weights"], a["hparams"]
    assert a["format"] == "gguf" and a["architecture"] == "llama" and hp["n_vocab"] == 2000
    assert hp["n_head_kv"] == [2, 2] and hp["key_length"] == 16
    assert w["embedding_bytes"] == 2000 * 64 * 2 and w["output_bytes"] == 2000 * 64 * 2
    assert w["per_layer_bytes"] == [64 * 64 * 2 + 64 * 4] * 2
    assert w["total_bytes"] == w["embedding_bytes"] + w["output_bytes"] + sum(w["per_layer_bytes"])
    assert kv_bytes_per_token(a) == 2 * (2 * 2 * 16) * 2  # layers x (kv heads x (k+v) x head_dim) x f16
    assert a["id"] == analyze(tmp_path / "m.gguf")["id"]  # path-independent, stable


def test_moe_analysis(tmp_path: Path) -> None:
    a = analyze(write_model(tmp_path / "moe.gguf", moe=8))
    w = a["weights"]
    exp = 8 * 128 * 64 * 2
    assert w["per_layer_expert_bytes"] == [exp, exp]
    assert w["active_bytes_per_token"] == w["total_bytes"] - 2 * exp + 2 * exp * 2 // 8


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b"GGML" + b[4:],  # wrong magic
        lambda b: b[:4] + struct.pack("<I", 9) + b[8:],  # unsupported version
        lambda b: b[:8] + struct.pack("<Q", 10**9) + b[16:],  # implausible tensor count
        lambda b: b[:200],  # truncated
    ],
)
def test_reader_rejects_malformed_files(tmp_path: Path, mutate: object) -> None:
    good = write_model(tmp_path / "ok.gguf").read_bytes()
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(mutate(good))  # type: ignore[operator]
    with pytest.raises(G.GGUFError):
        G.read_gguf(bad)


def test_plan_8b_fits_the_4090(desktop_root: Path) -> None:
    p = plan(workstation_caps(desktop_root), dense_8b(), Request(ctx=8192))
    assert p["mode"] == "gpu-full" and p["backend"] == "cuda" and p["device"] == "nvidia:0"
    assert p["engine_config"]["n_gpu_layers"] == 33 and p["quality"]["label"] == "lossless"
    assert p["memory"]["kv_bytes"] == 32 * 8 * 256 * 2 * 8192  # exact for full attention, f16
    b = p["bottlenecks"]
    assert b["classes"] == ["decode: device memory bandwidth"]
    assert b["decode_upper_bound_tokens_per_s"] == pytest.approx(
        1008e9 / p["memory"]["device_read_per_token_bytes"], 0.01
    )
    assert "uncalibrated" in p["estimate"]


def test_plan_70b_is_hybrid_and_bound_needs_ram_bandwidth(desktop_root: Path) -> None:
    p = plan(workstation_caps(desktop_root), dense_70b(), Request(ctx=4096))
    assert p["mode"] == "hybrid-layers" and 0 < p["engine_config"]["n_gpu_layers"] < 80
    assert p["memory"]["device_bytes"] + 1024**3 <= 24000 * 1024**2
    assert p["bottlenecks"]["decode_upper_bound_tokens_per_s"] is None  # DDR5 bandwidth not known
    assert any("host memory bandwidth" in c for c in p["bottlenecks"]["classes"])


def test_plan_moe_keeps_attention_on_gpu(desktop_root: Path) -> None:
    p = plan(workstation_caps(desktop_root), moe_120b(), Request(ctx=8192))
    assert p["mode"] == "hybrid-moe" and p["engine_config"]["n_gpu_layers"] == 37
    assert 0 < p["engine_config"]["n_cpu_moe"] <= 36


def test_plan_cpu_only_and_infeasible(vm_root: Path) -> None:
    from compute_fix import layout_for

    from ailx.compute.capabilities import collect_capabilities

    caps = collect_capabilities(layout_for(vm_root), use_nvml=False)  # 16 GB RAM, no GPU
    caps["live"]["memory_available_bytes"] = 14 * GiB
    p = plan(caps, dense_8b(), Request(ctx=4096))
    assert p["mode"] == "cpu" and p["backend"] == "cpu" and p["engine_config"]["n_gpu_layers"] == 0
    assert any("llama.cpp --variant cpu" in x for x in p["actions"])  # no engine installed in the fixture
    big = plan(caps, dense_70b(), Request(ctx=4096))
    assert not big["feasible"] and big["bottlenecks"]["classes"][0].startswith("capacity")
    assert any("smaller" in s for s in big["suggestions"])


def test_lossy_kv_requires_opt_in(desktop_root: Path) -> None:
    caps = workstation_caps(desktop_root)
    with pytest.raises(ValueError, match="lossy"):
        plan(caps, dense_8b(), Request(cache_type_k="q8_0", cache_type_v="q8_0"))
    p = plan(caps, dense_8b(), Request(ctx=8192, cache_type_k="q8_0", cache_type_v="q8_0", allow_lossy=True))
    assert p["quality"]["label"] == "lossy (opt-in)"
    assert p["memory"]["kv_bytes"] < plan(caps, dense_8b(), Request(ctx=8192))["memory"]["kv_bytes"]


def test_live_vram_limits_the_plan(desktop_root: Path) -> None:
    busy = workstation_caps(desktop_root, vram_free=3 * GiB)  # another process holds most of the VRAM
    assert plan(busy, dense_8b(), Request(ctx=8192))["mode"] != "gpu-full"
    assert plan(busy, dense_8b(), Request(ctx=8192, use_live=False))["mode"] == "gpu-full"
