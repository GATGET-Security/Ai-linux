# SPDX-License-Identifier: MIT
"""Adapter for the built-in synthetic workload."""

from __future__ import annotations

import json
import sys

from ailx.adapters import Parsed, RunContext
from ailx.core import Doc


class SyntheticAdapter:
    def build(self, workload: Doc, ctx: RunContext) -> list[str]:
        p = workload["params"]
        cmd = [
            sys.executable,
            "-m",
            "ailx.workloads.synthetic",
            "--mode",
            p.get("mode", "simulated"),
            "--seed",
            str(p.get("seed", ctx.seed)),
            "--order-index",
            str(ctx.order_index),
        ]
        for key in ("slowdown", "noise_cv", "drift_per_run", "periodic_amp", "periodic_period", "work_units"):
            if key in p:
                cmd += ["--" + key.replace("_", "-"), str(p[key])]
        return cmd

    def parse(self, stdout: str, stderr: str, workload: Doc, ctx: RunContext) -> Parsed:
        data = json.loads(stdout.strip().splitlines()[-1])
        m = data["metrics"]
        kind = "simulated" if data["mode"] == "simulated" else "hardware"
        notes = ["synthetic test workload; not a statement about AI workloads"]
        if kind == "simulated":
            notes.append("value drawn from a seeded distribution (no timing)")
        return Parsed(
            metrics={
                "duration_s": {"value": m["duration_s"], "unit": "s"},
                "work_rate": {"value": m["work_rate"], "unit": "units/s"},
            },
            tool_name="ailx-synthetic",
            tool_version="0.1.0",
            measurement_kind=kind,
            notes=notes,
        )
