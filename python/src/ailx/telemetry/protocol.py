# SPDX-License-Identifier: MIT
"""Helpers for writing ``ailx.sample-stream/v0`` lines (any language may implement the protocol)."""

from __future__ import annotations

import json
import signal
import sys
import time
from collections.abc import Callable
from typing import Any

SCHEMA = "ailx.sample-stream/v0"


def now_ns() -> int:
    return time.monotonic_ns()


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def header(
    sampler: str,
    version: str,
    metrics: list[dict[str, Any]],
    interval_s: float,
    available: bool = True,
    reason: str | None = None,
    simulated: bool = False,
) -> None:
    h: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": "header",
        "sampler": sampler,
        "version": version,
        "clock": "CLOCK_MONOTONIC",
        "available": available,
        "metrics": metrics,
        "interval_s": interval_s,
    }
    if reason:
        h["reason"] = reason
    if simulated:
        h["simulated"] = True
    emit(h)


def run_loop(read: Callable[[], dict[str, float | None]], interval_s: float) -> None:
    """Emit samples every ``interval_s`` until SIGTERM/SIGINT."""
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    next_t = time.monotonic()
    while not stop:
        emit({"kind": "sample", "t_ns": now_ns(), "values": read()})
        next_t += interval_s
        time.sleep(max(0.0, next_t - time.monotonic()))
    emit({"kind": "sample", "t_ns": now_ns(), "values": read()})  # final sample closes the window


def parse_interval(argv: list[str], default: float) -> float:
    if "--interval" in argv:
        return float(argv[argv.index("--interval") + 1])
    return default
