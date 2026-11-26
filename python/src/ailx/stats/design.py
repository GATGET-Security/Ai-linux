# SPDX-License-Identifier: MIT
"""Experimental designs."""

from __future__ import annotations

import numpy as np


def rmit_schedule(arms: list[str], blocks: int, runs_per_block: int, seed: int) -> list[dict[str, int | str]]:
    """Randomized multiple interleaved trials.

    Every block contains each arm ``runs_per_block`` times in a random order, so slow
    drift and periodic interference hit all arms alike instead of biasing one of them.
    The seed is part of the pre-registered campaign, which makes the order reproducible.
    """
    if len(arms) < 2 or len(set(arms)) != len(arms):
        raise ValueError("need at least two distinct arms")
    if blocks < 1 or runs_per_block < 1:
        raise ValueError("blocks and runs_per_block must be >= 1")
    rng = np.random.default_rng(seed)
    schedule: list[dict[str, int | str]] = []
    for block in range(blocks):
        slots = [arm for arm in arms for _ in range(runs_per_block)]
        for pos in rng.permutation(len(slots)):
            schedule.append({"index": len(schedule), "block": block, "arm": slots[int(pos)]})
    return schedule
