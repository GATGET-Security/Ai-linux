# SPDX-License-Identifier: MIT
"""Comparisons, decisions, FDR and power analysis.

Decision vocabulary (deliberately asymmetric):

* ``improvement`` / ``regression`` — significant (Mann-Whitney U, alpha) *and* the
  effect CI lies entirely beyond the decision threshold ``max(e_min, 2 * noise_floor)``.
* ``significant-below-threshold`` — detectable but smaller than the minimum effect of interest.
* ``equivalent-within-margin`` — only if an equivalence margin was pre-registered and the
  whole CI lies inside it.
* ``no-detectable-difference`` — absence of evidence. This is **not** a finding of
  equivalence and must never be reported as one.
* ``insufficient-data`` — fewer than 3 successful runs in an arm.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
from scipy import stats as _st

Better = Literal["higher", "lower"]
MIN_RUNS = 3


@dataclass
class Comparison:
    metric: str
    better: Better
    n_baseline: int
    n_candidate: int
    median_baseline: float
    median_candidate: float
    ratio: float  # median(candidate) / median(baseline)
    improvement: float  # signed fraction, >0 = better
    improvement_ci: tuple[float, float]
    hodges_lehmann_shift: float  # candidate - baseline, in metric units
    p_value: float
    p_adjusted: float | None
    threshold: float
    calibrated: bool
    ci_method: str
    verdict: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["improvement_ci"] = list(self.improvement_ci)
        return d


def _ratio_stat(x: np.ndarray, y: np.ndarray, axis: int = -1) -> np.ndarray:
    result: np.ndarray = np.median(y, axis=axis) / np.median(x, axis=axis)
    return result


def ratio_ci(
    baseline: np.ndarray, candidate: np.ndarray, confidence: float, n_resamples: int, seed: int
) -> tuple[float, float, str]:
    """Bootstrap CI of median(candidate)/median(baseline); BCa with percentile fallback."""
    for method in ("BCa", "percentile"):
        try:
            res = _st.bootstrap(
                (baseline, candidate),
                _ratio_stat,
                vectorized=True,
                paired=False,
                confidence_level=confidence,
                n_resamples=n_resamples,
                method=method,
                random_state=np.random.default_rng(seed),
            )
            lo, hi = float(res.confidence_interval.low), float(res.confidence_interval.high)
            if np.isfinite(lo) and np.isfinite(hi):
                return lo, hi, method
        except (ValueError, FloatingPointError):
            continue
    r = float(_ratio_stat(baseline, candidate))
    return r, r, "degenerate"


def hodges_lehmann(baseline: np.ndarray, candidate: np.ndarray) -> float:
    return float(np.median(np.subtract.outer(candidate, baseline)))


def classify(
    p: float, ci: tuple[float, float], alpha: float, threshold: float, equivalence_margin: float | None
) -> str:
    lo, hi = ci
    if p < alpha and lo > threshold:
        return "improvement"
    if p < alpha and hi < -threshold:
        return "regression"
    if p < alpha:
        return "significant-below-threshold"
    if equivalence_margin is not None and -equivalence_margin < lo and hi < equivalence_margin:
        return "equivalent-within-margin"
    return "no-detectable-difference"


def compare(
    metric: str,
    baseline: list[float] | np.ndarray,
    candidate: list[float] | np.ndarray,
    better: Better,
    alpha: float = 0.05,
    e_min: float = 0.02,
    noise_floor: float | None = None,
    equivalence_margin: float | None = None,
    n_resamples: int = 10000,
    seed: int = 0,
) -> Comparison:
    b = np.asarray(baseline, dtype=float)
    c = np.asarray(candidate, dtype=float)
    b, c = b[np.isfinite(b)], c[np.isfinite(c)]
    threshold = max(e_min, 2.0 * noise_floor) if noise_floor is not None else e_min
    notes: list[str] = []
    if noise_floor is None:
        notes.append("uncalibrated: no A/A noise floor; threshold = e_min only")
    if len(b) < MIN_RUNS or len(c) < MIN_RUNS:
        nan = float("nan")
        return Comparison(
            metric,
            better,
            len(b),
            len(c),
            nan,
            nan,
            nan,
            nan,
            (nan, nan),
            nan,
            nan,
            None,
            threshold,
            noise_floor is not None,
            "none",
            "insufficient-data",
            notes,
        )
    mb, mc = float(np.median(b)), float(np.median(c))
    lo, hi, method = ratio_ci(b, c, 1 - alpha, n_resamples, seed)
    ratio = mc / mb
    if better == "higher":
        imp, imp_ci = ratio - 1.0, (lo - 1.0, hi - 1.0)
    else:
        imp, imp_ci = 1.0 / ratio - 1.0, (1.0 / hi - 1.0, 1.0 / lo - 1.0)
    p = float(_st.mannwhitneyu(c, b, alternative="two-sided").pvalue)
    verdict = classify(p, imp_ci, alpha, threshold, equivalence_margin)
    if verdict == "no-detectable-difference":
        notes.append("absence of evidence is not evidence of equivalence")
    return Comparison(
        metric,
        better,
        len(b),
        len(c),
        mb,
        mc,
        ratio,
        imp,
        imp_ci,
        hodges_lehmann(b, c),
        p,
        None,
        threshold,
        noise_floor is not None,
        method,
        verdict,
        notes,
    )


def fdr_bh(comparisons: list[Comparison], alpha: float, equivalence_margin: float | None = None) -> None:
    """Benjamini-Hochberg adjustment in place; verdicts are re-derived from adjusted p-values."""
    valid = [c for c in comparisons if c.verdict != "insufficient-data"]
    if not valid:
        return
    adj = _st.false_discovery_control([c.p_value for c in valid], method="bh")
    for c, pa in zip(valid, adj, strict=True):
        c.p_adjusted = float(pa)
        c.verdict = classify(float(pa), c.improvement_ci, alpha, c.threshold, equivalence_margin)
        if "fdr: benjamini-hochberg" not in c.notes:
            c.notes.append("fdr: benjamini-hochberg")


def noise_floor_from_aa(comparison: Comparison) -> float:
    """Systematic noise floor of an A/A comparison: |point estimate| of the (null) effect.

    Sampling uncertainty is already carried by each campaign's own confidence interval, so the
    floor only needs to capture systematic differences between identical arms (order effects,
    drift the design did not cancel). Using the CI bound here would double-count sampling noise
    and, with the 2x factor of the decision rule, make realistic effects undetectable.
    """
    return float(abs(comparison.improvement))


def rsd_stop(samples: list[float], threshold: float, min_runs: int, max_runs: int) -> bool:
    """PTS-style dynamic run count: stop once relative SD < threshold (or max reached)."""
    n = len(samples)
    if n >= max_runs:
        return True
    if n < max(min_runs, 2):
        return False
    arr = np.asarray(samples, dtype=float)
    mean = float(np.mean(arr))
    return mean != 0 and float(np.std(arr, ddof=1)) / abs(mean) < threshold


def lognormal_sigma(cv: float) -> float:
    return float(np.sqrt(np.log1p(cv * cv)))


def power_n(
    cv: float,
    effect: float,
    e_min: float,
    alpha: float = 0.05,
    power: float = 0.8,
    noise_floor: float | None = None,
    sims: int = 200,
    n_max: int = 60,
    n_resamples: int = 999,
    seed: int = 0,
) -> dict[str, Any]:
    """Smallest runs-per-arm whose simulated detection rate reaches ``power``.

    Simulates log-normal run-level noise with coefficient of variation ``cv`` and a true
    improvement ``effect`` (higher-is-better), and applies exactly the decision rule of
    :func:`compare`. Returns ``{"n": None}`` if even ``n_max`` is insufficient.
    """
    rng = np.random.default_rng(seed)
    sigma = lognormal_sigma(cv)

    def rate(n: int) -> float:
        hits = 0
        for i in range(sims):
            a = rng.lognormal(0.0, sigma, n)
            b = rng.lognormal(0.0, sigma, n) * (1 + effect)
            r = compare("m", a, b, "higher", alpha, e_min, noise_floor, None, n_resamples, seed + i)
            hits += r.verdict == "improvement"
        return hits / sims

    # Conservative target: the simulated rate must exceed ``power`` by 1.5 Monte-Carlo standard
    # errors, otherwise small simulation counts systematically under-size campaigns.
    target = min(0.995, power + 1.5 * float(np.sqrt(power * (1 - power) / sims)))
    lo, hi = MIN_RUNS, n_max
    if rate(hi) < target:
        return {"n": None, "cv": cv, "effect": effect, "e_min": e_min, "power": power, "sims": sims}
    while lo < hi:
        mid = (lo + hi) // 2
        if rate(mid) >= target:
            hi = mid
        else:
            lo = mid + 1
    return {
        "n": lo,
        "cv": cv,
        "effect": effect,
        "e_min": e_min,
        "power": power,
        "sims": sims,
        "simulated_target": target,
        "note": "simulation-based; assumes log-normal i.i.d. run-level noise; conservative MC margin",
    }
