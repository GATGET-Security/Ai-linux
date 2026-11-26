# SPDX-License-Identifier: MIT
"""Fake wall-power meter for tests. Marked ``simulated`` in its header; never use for claims."""

from __future__ import annotations

import random
import sys

from ailx.telemetry.protocol import header, parse_interval, run_loop


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    interval = parse_interval(argv, 0.2)
    rng = random.Random(0)
    energy = 0.0
    header(
        "meter_fake",
        "0.1.0",
        [
            {"name": "wall.power_w", "unit": "W", "type": "gauge"},
            {"name": "wall.energy_j", "unit": "J", "type": "counter"},
        ],
        interval,
        simulated=True,
    )

    def read() -> dict[str, float | None]:
        nonlocal energy
        p = 100.0 + rng.gauss(0, 2)
        energy += p * interval
        return {"wall.power_w": p, "wall.energy_j": energy}

    run_loop(read, interval)


if __name__ == "__main__":
    main()
