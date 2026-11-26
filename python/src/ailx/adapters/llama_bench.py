# SPDX-License-Identifier: MIT
"""llama.cpp ``llama-bench`` adapter (JSON output).

Metrics: ``pp_tokens_per_s`` (prompt processing, test with n_gen=0) and ``tg_tokens_per_s``
(token generation, test with n_prompt=0). The run-level value is the median of llama-bench's
internal repetitions; the repetitions are kept as ``samples``. llama-bench always generates
exactly ``n_gen`` tokens, so the output length is fixed by construction (verified below).
"""

from __future__ import annotations

import json
import statistics

from ailx.adapters import Parsed, RunContext
from ailx.core import Doc
from ailx.workloads import resolve

OPTIONAL_FLAGS = {
    "threads": "-t",
    "n_gpu_layers": "-ngl",
    "flash_attn": "-fa",
    "n_cpu_moe": "-ncmoe",
    "batch_size": "-b",
    "ubatch_size": "-ub",
    "cache_type_k": "-ctk",
    "cache_type_v": "-ctv",
}


class LlamaBenchAdapter:
    def build(self, workload: Doc, ctx: RunContext) -> list[str]:
        p = workload["params"]
        model = workload.get("model") or {}
        cmd = [
            resolve(workload["runtime"]["binary"]),
            "-m",
            resolve(model["path"]),
            "-p",
            str(p.get("n_prompt", 512)),
            "-n",
            str(p.get("n_gen", 128)),
            "-r",
            str(p.get("repetitions", 5)),
            "-o",
            "json",
        ]
        for key, flag in OPTIONAL_FLAGS.items():
            if key in p:
                cmd += [flag, str(p[key])]
        cmd += [str(x) for x in p.get("extra_args", [])]
        return cmd

    def parse(self, stdout: str, stderr: str, workload: Doc, ctx: RunContext) -> Parsed:
        start = stdout.find("[")
        entries = json.loads(stdout[start:]) if start >= 0 else []
        p = workload["params"]
        metrics: dict[str, dict[str, object]] = {}
        version = None
        notes: list[str] = []
        for e in entries:
            version = f"{e.get('build_commit')} (build {e.get('build_number')})"
            samples = [float(x) for x in e.get("samples_ts", [])]
            if not samples:
                continue
            if e.get("n_gen", 0) == 0 and e.get("n_prompt", 0) > 0:
                metrics["pp_tokens_per_s"] = {
                    "value": statistics.median(samples),
                    "unit": "tokens/s",
                    "samples": samples,
                }
            elif e.get("n_prompt", 0) == 0 and e.get("n_gen", 0) > 0:
                if int(e["n_gen"]) != int(p.get("n_gen", 128)):
                    notes.append(f"n_gen mismatch: requested {p.get('n_gen')}, got {e['n_gen']}")
                metrics["tg_tokens_per_s"] = {
                    "value": statistics.median(samples),
                    "unit": "tokens/s",
                    "samples": samples,
                }
        status = "ok" if metrics else "invalid"
        return Parsed(
            metrics=metrics,
            tool_name="llama-bench",
            tool_version=version,
            status=status,
            detail=None if metrics else "no parsable llama-bench results",
            notes=notes,
        )
