# SPDX-License-Identifier: MIT
"""Statistics acceptance tests (simulation-based; no hardware involved)."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest
from scipy import stats as st

from ailx.stats import compare, fdr_bh, power_n, rmit_schedule
from ailx.stats.inference import lognormal_sigma
from ailx.workloads.synthetic import simulated_value

ALPHA = 0.05


def test_rmit_schedule_is_balanced_and_reproducible() -> None:
    s = rmit_schedule(["A", "B", "C"], blocks=4, runs_per_block=2, seed=9)
    assert len(s) == 24 and [x["index"] for x in s] == list(range(24))
    for b in range(4):
        assert Counter(x["arm"] for x in s if x["block"] == b) == {"A": 2, "B": 2, "C": 2}
    assert s == rmit_schedule(["A", "B", "C"], 4, 2, 9)
    assert s != rmit_schedule(["A", "B", "C"], 4, 2, 10)
    with pytest.raises(ValueError):
        rmit_schedule(["A"], 1, 1, 0)


def test_direction_and_vocabulary() -> None:
    rng = np.random.default_rng(1)
    base = rng.lognormal(0, 0.01, 12)
    assert compare("tps", base, base * 1.06, "higher", n_resamples=999).verdict == "improvement"
    assert compare("tps", base, base * 0.94, "higher", n_resamples=999).verdict == "regression"
    assert compare("lat_ms", base, base * 0.94, "lower", n_resamples=999).verdict == "improvement"
    small = compare("tps", base, base * 1.005, "higher", n_resamples=999, e_min=0.02)
    assert small.verdict in ("significant-below-threshold", "no-detectable-difference")
    assert compare("tps", base[:2], base[:2], "higher").verdict == "insufficient-data"


def test_non_significant_is_not_equivalence() -> None:
    rng = np.random.default_rng(2)
    a, b = rng.lognormal(0, 0.05, 5), rng.lognormal(0, 0.05, 5)
    c = compare("m", a, b, "higher", n_resamples=999)
    assert c.verdict == "no-detectable-difference"
    assert any("not evidence of equivalence" in n for n in c.notes)
    rng = np.random.default_rng(3)
    a, b = rng.lognormal(0, 0.005, 30), rng.lognormal(0, 0.005, 30)
    eq = compare("m", a, b, "higher", n_resamples=999, equivalence_margin=0.02)
    assert eq.verdict == "equivalent-within-margin"  # only with a pre-registered margin


@pytest.mark.slow
def test_aa_false_claim_rate() -> None:
    """A/A acceptance: over 1000 simulated campaigns, significant differences ≤ alpha + 0.02, claims ≤ alpha + 0.02."""
    rng = np.random.default_rng(42)
    sims, sig, claims = 1000, 0, 0
    for i, cv in enumerate(np.linspace(0.01, 0.05, 1000)):
        s = lognormal_sigma(float(cv))
        a, b = rng.lognormal(0, s, 12), rng.lognormal(0, s, 12)
        sig += st.mannwhitneyu(a, b).pvalue < ALPHA
        if i % 5 == 0:  # full decision path (bootstrap) on a subset to keep CI time low
            claims += compare("m", a, b, "higher", n_resamples=499, seed=i).verdict in ("improvement", "regression")
    assert sig / sims <= ALPHA + 0.02
    assert claims / (sims / 5) <= ALPHA + 0.02


@pytest.mark.slow
def test_five_percent_effect_power() -> None:
    est = power_n(cv=0.02, effect=0.05, e_min=0.02, sims=80, seed=1)
    n = est["n"]
    assert n is not None and n <= 30
    rng = np.random.default_rng(7)
    s = lognormal_sigma(0.02)
    hits = sum(
        compare("m", rng.lognormal(0, s, n), rng.lognormal(0, s, n) * 1.05, "higher", n_resamples=499, seed=k).verdict
        == "improvement"
        for k in range(150)
    )
    assert hits / 150 >= 0.75  # independent re-check of the 0.8 target with Monte-Carlo slack


@pytest.mark.slow
def test_rmit_protects_against_drift_where_sequential_fails() -> None:
    """Identical arms, strong linear drift: sequential AAA…BBB produces false claims, RMIT does not."""

    def false_claim_rate(order: str, sims: int = 120) -> float:
        bad = 0
        for k in range(sims):
            if order == "rmit":
                sched = rmit_schedule(["A", "B"], 8, 2, seed=k)
            else:
                sched = [{"index": i, "arm": "A" if i < 16 else "B"} for i in range(32)]
            vals: dict[str, list[float]] = {"A": [], "B": []}
            for slot in sched:
                vals[str(slot["arm"])].append(simulated_value(k, int(slot["index"]), 1.0, 0.01, 0.004, 0.0, 7.0))
            v = compare("m", vals["A"], vals["B"], "higher", n_resamples=299, seed=k).verdict
            bad += v in ("improvement", "regression")
        return bad / sims

    assert false_claim_rate("sequential") > 0.5
    assert false_claim_rate("rmit") <= ALPHA + 0.03


def test_fdr_bh_adjusts_and_rederives() -> None:
    rng = np.random.default_rng(4)
    base = rng.lognormal(0, 0.01, 12)
    comps = [compare(f"m{i}", base, base * f, "higher", n_resamples=499) for i, f in enumerate((1.06, 1.0, 1.001))]
    fdr_bh(comps, ALPHA)
    assert all(c.p_adjusted is not None and c.p_adjusted >= c.p_value - 1e-12 for c in comps)
    assert comps[0].verdict == "improvement"
