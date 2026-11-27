# SPDX-License-Identifier: MIT
"""Statistics for benchmark campaigns (WP4).

References: randomized multiple interleaved trials (Abedi & Brecht 2017) [S-50];
effect-size confidence intervals (Kalibera & Jones 2013) [S-48]; nonparametric
reporting (Hoefler & Belli 2015) [S-49]; Benjamini-Hochberg FDR control.
"""

from ailx.stats.design import rmit_schedule
from ailx.stats.inference import Comparison, compare, fdr_bh, noise_floor_from_aa, power_n, rsd_stop

__all__ = ["Comparison", "compare", "fdr_bh", "noise_floor_from_aa", "power_n", "rmit_schedule", "rsd_stop"]
