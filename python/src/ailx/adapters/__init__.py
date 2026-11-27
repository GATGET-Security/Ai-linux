# SPDX-License-Identifier: MIT
"""Tool adapters: build a command line and parse the tool's machine-readable output (WP3).

Every adapter maps tool output onto the pinned metric definitions of
``docs/architecture/benchmarking.md`` §3 and records the tool version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ailx.core import Doc


@dataclass
class RunContext:
    order_index: int
    seed: int
    workdir: Path


@dataclass
class Parsed:
    metrics: dict[str, dict[str, Any]]
    tool_name: str
    tool_version: str | None = None
    measurement_kind: str = "hardware"
    status: str = "ok"
    detail: str | None = None
    notes: list[str] = field(default_factory=list)


class Adapter(Protocol):
    def build(self, workload: Doc, ctx: RunContext) -> list[str]: ...
    def parse(self, stdout: str, stderr: str, workload: Doc, ctx: RunContext) -> Parsed: ...


def get_adapter(name: str) -> Adapter:
    from ailx.adapters import llama_bench, synthetic, vllm_bench

    table: dict[str, Adapter] = {
        "synthetic": synthetic.SyntheticAdapter(),
        "llama-bench": llama_bench.LlamaBenchAdapter(),
        "vllm-bench-serve": vllm_bench.VllmBenchServeAdapter(),
    }
    return table[name]
