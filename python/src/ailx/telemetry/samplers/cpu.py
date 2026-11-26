# SPDX-License-Identifier: MIT
"""CPU utilisation (aggregate jiffies counters) and per-CPU current frequency."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from ailx.telemetry.protocol import header, parse_interval, run_loop

FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def read_cpu() -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    try:
        first = Path("/proc/stat").read_text().splitlines()[0].split()
    except OSError:
        return out
    hz = os.sysconf("SC_CLK_TCK")
    for name, val in zip(FIELDS, first[1:], strict=False):
        out[f"cpu.{name}_s"] = int(val) / hz
    freqs = []
    for f in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
        with contextlib.suppress(OSError, ValueError):
            freqs.append(int(f.read_text()))
    if freqs:
        out["cpu.freq_mean_khz"] = sum(freqs) / len(freqs)
        out["cpu.freq_min_khz"] = float(min(freqs))
    return out


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    interval = parse_interval(argv, 0.5)
    first = read_cpu()
    metrics = [
        {"name": k, "unit": "s" if k.endswith("_s") else "kHz", "type": "counter" if k.endswith("_s") else "gauge"}
        for k in first
    ]
    header("cpu", "0.1.0", metrics, interval, available=bool(first), reason=None if first else "/proc/stat unreadable")
    if first:
        run_loop(read_cpu, interval)


if __name__ == "__main__":
    main()
