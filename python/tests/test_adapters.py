# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ailx.adapters import RunContext, get_adapter

FIX = Path(__file__).parent / "fixtures"


def wl(adapter: str, params: dict, policy: str = "fixed") -> dict:  # type: ignore[type-arg]
    return {
        "schema": "ailx.workload/v0",
        "name": "t",
        "version": 1,
        "type": "llm-single",
        "adapter": adapter,
        "params": params,
        "model": {"path": "/models/m.gguf"},
        "runtime": {"binary": "/bin/tool"},
        "output_len_policy": policy,
        "metrics": [{"name": "x", "unit": "u", "better": "higher"}],
        "warmup_runs": 0,
        "timeout_s": 10,
    }


def test_llama_bench_command_and_parse(tmp_path: Path) -> None:
    a = get_adapter("llama-bench")
    w = wl("llama-bench", {"n_prompt": 64, "n_gen": 16, "repetitions": 3, "threads": 2, "n_gpu_layers": 99})
    ctx = RunContext(0, 0, tmp_path)
    cmd = a.build(w, ctx)
    assert cmd[:3] == ["/bin/tool", "-m", "/models/m.gguf"] and "-ngl" in cmd and cmd[cmd.index("-o") + 1] == "json"
    p = a.parse((FIX / "llama-bench-98cc730.json").read_text(), "", w, ctx)
    assert p.status == "ok" and set(p.metrics) == {"pp_tokens_per_s", "tg_tokens_per_s"}
    assert len(p.metrics["tg_tokens_per_s"]["samples"]) == 3
    assert p.tool_version and p.tool_version.startswith("98cc730")


def test_llama_bench_detects_n_gen_mismatch(tmp_path: Path) -> None:
    a = get_adapter("llama-bench")
    w = wl("llama-bench", {"n_prompt": 64, "n_gen": 32})
    p = a.parse((FIX / "llama-bench-98cc730.json").read_text(), "", w, RunContext(0, 0, tmp_path))
    assert any("n_gen mismatch" in n for n in p.notes)


def test_llama_bench_garbage_is_invalid(tmp_path: Path) -> None:
    p = get_adapter("llama-bench").parse(
        "error: model not found", "", wl("llama-bench", {}), RunContext(0, 0, tmp_path)
    )
    assert p.status == "invalid"


def test_vllm_command_and_parse(tmp_path: Path) -> None:
    a = get_adapter("vllm-bench-serve")
    w = wl("vllm-bench-serve", {"model": "m", "input_len": 128, "output_len": 32, "num_prompts": 4})
    ctx = RunContext(3, 7, tmp_path)
    cmd = a.build(w, ctx)
    assert cmd[1:3] == ["bench", "serve"] and "--ignore-eos" in cmd and "--save-detailed" in cmd
    shutil.copy(FIX / "vllm-bench-serve-constructed.json", tmp_path / "vllm-bench-3.json")
    p = a.parse("", "", w, ctx)
    assert p.status == "ok" and p.metrics["output_tokens_per_s"]["value"] == 80.0
    assert p.metrics["ttft_p99_ms"]["unit"] == "ms"


def test_vllm_fixed_length_violation_is_invalid(tmp_path: Path) -> None:
    a = get_adapter("vllm-bench-serve")
    w = wl("vllm-bench-serve", {"model": "m", "input_len": 128, "output_len": 32, "num_prompts": 4})
    data = json.loads((FIX / "vllm-bench-serve-constructed.json").read_text())
    data["output_lens"] = [32, 32, 17, 32]
    (tmp_path / "vllm-bench-0.json").write_text(json.dumps(data))
    assert a.parse("", "", w, RunContext(0, 0, tmp_path)).status == "invalid"
    data["completed"] = 3
    data["output_lens"] = [32, 32, 32]
    (tmp_path / "vllm-bench-0.json").write_text(json.dumps(data))
    assert a.parse("", "", w, RunContext(0, 0, tmp_path)).status == "failed"
