# SPDX-License-Identifier: MIT
"""vLLM ``vllm bench serve`` adapter (client side; optional server lifecycle via the runner).

Uses the random dataset with fixed input/output lengths; with ``output_len_policy = fixed``
``--ignore-eos`` is passed and achieved output lengths are verified from the detailed result.
Metric names map to the pinned definitions; vLLM's own definitions are recorded via the
tool version (ITL definitions differ between tools [S-58]).
"""

from __future__ import annotations

import json
from pathlib import Path

from ailx.adapters import Parsed, RunContext
from ailx.core import Doc
from ailx.workloads import resolve

MAP = {  # vLLM result key -> (our metric, unit)
    "request_throughput": ("request_throughput", "requests/s"),
    "output_throughput": ("output_tokens_per_s", "tokens/s"),
    "total_token_throughput": ("total_tokens_per_s", "tokens/s"),
    "median_ttft_ms": ("ttft_p50_ms", "ms"),
    "p99_ttft_ms": ("ttft_p99_ms", "ms"),
    "median_tpot_ms": ("tpot_p50_ms", "ms"),
    "p99_tpot_ms": ("tpot_p99_ms", "ms"),
    "median_itl_ms": ("itl_p50_ms", "ms"),
    "p99_itl_ms": ("itl_p99_ms", "ms"),
    "median_e2el_ms": ("e2e_p50_ms", "ms"),
    "p99_e2el_ms": ("e2e_p99_ms", "ms"),
}


def result_file(ctx: RunContext) -> Path:
    return ctx.workdir / f"vllm-bench-{ctx.order_index}.json"


class VllmBenchServeAdapter:
    def build(self, workload: Doc, ctx: RunContext) -> list[str]:
        p = workload["params"]
        out = result_file(ctx)
        cmd = [
            resolve(workload["runtime"]["binary"]),
            "bench",
            "serve",
            "--backend",
            p.get("backend", "vllm"),
            "--base-url",
            p.get("base_url", "http://127.0.0.1:8000"),
            "--model",
            p["model"],
            "--dataset-name",
            "random",
            "--random-input-len",
            str(p["input_len"]),
            "--random-output-len",
            str(p["output_len"]),
            "--num-prompts",
            str(p["num_prompts"]),
            "--request-rate",
            str(p.get("request_rate", "inf")),
            "--seed",
            str(p.get("seed", ctx.seed)),
            "--save-result",
            "--save-detailed",
            "--result-dir",
            str(out.parent),
            "--result-filename",
            out.name,
        ]
        if workload["output_len_policy"] == "fixed":
            cmd.append("--ignore-eos")
        if "max_concurrency" in p:
            cmd += ["--max-concurrency", str(p["max_concurrency"])]
        cmd += [str(x) for x in p.get("extra_args", [])]
        return cmd

    def parse(self, stdout: str, stderr: str, workload: Doc, ctx: RunContext) -> Parsed:
        path = result_file(ctx)
        if not path.exists():
            return Parsed({}, "vllm bench serve", status="invalid", detail=f"result file {path.name} missing")
        data = json.loads(path.read_text())
        metrics = {
            ours: {"value": float(data[key]), "unit": unit}
            for key, (ours, unit) in MAP.items()
            if data.get(key) is not None
        }
        notes: list[str] = []
        status, detail = "ok", None
        p = workload["params"]
        if int(data.get("completed", 0)) < int(p["num_prompts"]):
            status, detail = "failed", f"completed {data.get('completed')} of {p['num_prompts']} requests"
        lens = data.get("output_lens")
        if workload["output_len_policy"] == "fixed":
            if not lens:
                notes.append("output lengths not verifiable (no detailed output_lens)")
            elif any(int(x) != int(p["output_len"]) for x in lens):
                status, detail = "invalid", "fixed output length requested but achieved lengths differ"
        return Parsed(
            metrics,
            "vllm bench serve",
            data.get("vllm_version") or data.get("version"),
            status=status,
            detail=detail,
            notes=notes,
        )
