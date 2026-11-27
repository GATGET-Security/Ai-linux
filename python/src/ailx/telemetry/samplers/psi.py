# SPDX-License-Identifier: MIT
"""Pressure stall information sampler (/proc/pressure)."""

from __future__ import annotations

import sys
from pathlib import Path

from ailx.telemetry.protocol import header, parse_interval, run_loop

RESOURCES = ("cpu", "memory", "io")


def read_psi(base: Path = Path("/proc/pressure")) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for res in RESOURCES:
        try:
            lines = (base / res).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            kind, *fields = line.split()
            kv = dict(f.split("=") for f in fields)
            out[f"{res}.{kind}.avg10"] = float(kv["avg10"])
            out[f"{res}.{kind}.total_us"] = float(kv["total"])
    return out


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    interval = parse_interval(argv, 0.5)
    first = read_psi()
    if not first:
        header("psi", "0.1.0", [], interval, available=False, reason="/proc/pressure not available (CONFIG_PSI off?)")
        return
    metrics = [
        {
            "name": k,
            "unit": "us" if k.endswith("total_us") else "%",
            "type": "counter" if k.endswith("total_us") else "gauge",
        }
        for k in first
    ]
    header("psi", "0.1.0", metrics, interval)
    run_loop(read_psi, interval)


if __name__ == "__main__":
    main()
