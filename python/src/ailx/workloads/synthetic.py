# SPDX-License-Identifier: MIT
"""Synthetic workload: the harness's own test subject (never a statement about real hardware).

Modes:

* ``simulated`` — draws a run-level value from a seeded log-normal distribution with a
  controllable effect (``slowdown``), noise (``noise_cv``), linear ``drift`` per run index
  and ``periodic`` interference. No timing involved; results carry
  ``measurement_kind = "simulated"``. Used to validate statistics end-to-end.
* ``compute`` — runs a deterministic CPU/memory kernel (numpy matmul) ``work_units *
  slowdown`` times and reports measured wall time. Real timing on whatever machine runs it.

Output: one JSON object on stdout ``{"metrics": {"duration_s": ..., "work_rate": ...}, ...}``.
"""

from __future__ import annotations

import argparse
import json
import math
import time

import numpy as np


def simulated_value(
    seed: int,
    order_index: int,
    slowdown: float,
    noise_cv: float,
    drift_per_run: float,
    periodic_amp: float,
    periodic_period: float,
) -> float:
    rng = np.random.default_rng([seed, order_index])
    sigma = math.sqrt(math.log1p(noise_cv * noise_cv))
    base = 1.0 * slowdown * float(rng.lognormal(0.0, sigma))
    base *= 1.0 + drift_per_run * order_index
    if periodic_amp:
        base *= 1.0 + periodic_amp * math.sin(2 * math.pi * order_index / periodic_period)
    return base


def compute_kernel(work_units: int, slowdown: float, size: int = 192) -> float:
    a = np.random.default_rng(0).standard_normal((size, size))
    reps = max(1, round(work_units * slowdown))
    t0 = time.perf_counter()
    acc = a
    for _ in range(reps):
        acc = np.tanh(acc @ a / size)
    _ = float(acc[0, 0])
    return time.perf_counter() - t0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["simulated", "compute"], default="simulated")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--order-index", type=int, default=0)
    ap.add_argument("--slowdown", type=float, default=1.0)
    ap.add_argument("--noise-cv", type=float, default=0.02)
    ap.add_argument("--drift-per-run", type=float, default=0.0)
    ap.add_argument("--periodic-amp", type=float, default=0.0)
    ap.add_argument("--periodic-period", type=float, default=7.0)
    ap.add_argument("--work-units", type=int, default=50)
    a = ap.parse_args(argv)
    if a.mode == "simulated":
        d = simulated_value(
            a.seed, a.order_index, a.slowdown, a.noise_cv, a.drift_per_run, a.periodic_amp, a.periodic_period
        )
    else:
        d = compute_kernel(a.work_units, a.slowdown)
    print(json.dumps({"mode": a.mode, "metrics": {"duration_s": d, "work_rate": a.work_units / d}}))


if __name__ == "__main__":
    main()
