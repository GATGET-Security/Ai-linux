# SPDX-License-Identifier: MIT
"""Campaigns: pre-registration (plan), RMIT execution (run) and analysis (analyze)."""

from __future__ import annotations

import contextlib
import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from ailx import __version__
from ailx.core import Doc, seal, utcnow, validate
from ailx.probe import collect_fingerprint, collect_hardware_profile
from ailx.runner import execute_run
from ailx.stats import compare, fdr_bh, noise_floor_from_aa, rmit_schedule
from ailx.store import Store
from ailx.system.integration import platform_info, profile_applied
from ailx.system.paths import load_layout
from ailx.workloads import apply_overrides, bundle, load_workload, resolve, workload_sha256

DECISION_RULE = (
    "Per arm vs baseline and metric: Mann-Whitney U (two-sided, alpha; BH-adjusted if fdr=bh) and a BCa "
    "bootstrap CI of the ratio of medians. 'improvement'/'regression' only if significant and the whole "
    "CI lies beyond max(e_min, 2*noise_floor), where noise_floor is the |A/A effect point estimate| of the "
    "referenced calibration. Non-significant results are 'no-detectable-difference' "
    "(not equivalence) unless a pre-registered equivalence margin contains the whole CI."
)


def plan(spec: Doc, store: Store, base_dir: Path | None = None, calibration_id: str | None = None) -> Doc:
    """Freeze a campaign spec into a pre-registered campaign document."""
    spec = copy.deepcopy(spec)
    if calibration_id:
        cal = store.get(calibration_id)
        if cal.get("schema") != "ailx.analysis/v0" or not cal.get("calibration"):
            raise ValueError(f"{calibration_id} is not an A/A calibration analysis")
        spec["calibration_id"] = calibration_id
    wl = spec.pop("workload")
    if isinstance(wl, str):
        wl = load_workload((base_dir or Path.cwd()) / wl)
    validate(wl, "ailx.workload/v0")
    arms = spec["arms"]
    ids = [a["id"] for a in arms]
    baseline = spec.get("baseline_arm", ids[0])
    if baseline not in ids:
        raise ValueError(f"baseline arm {baseline!r} not among arms {ids}")
    metric_names = {m["name"] for m in wl["metrics"]}
    for m in [spec["primary_metric"], *spec.get("guard_metrics", [])]:
        if m not in metric_names:
            raise ValueError(f"metric {m!r} not defined by workload {wl['name']}")
    design = spec["design"]
    doc: Doc = {
        "schema": "ailx.campaign/v0",
        "created_at": utcnow(),
        "purpose": spec["purpose"],
        "title": spec["title"],
        "hypothesis_ids": spec.get("hypothesis_ids", []),
        "workload": wl,
        "workload_sha256": workload_sha256(wl),
        "arms": arms,
        "baseline_arm": baseline,
        "design": {
            "type": "rmit",
            "blocks": design["blocks"],
            "runs_per_block": design["runs_per_block"],
            "seed": design["seed"],
        },
        "schedule": rmit_schedule(ids, design["blocks"], design["runs_per_block"], design["seed"]),
        "primary_metric": spec["primary_metric"],
        "guard_metrics": spec.get("guard_metrics", []),
        "e_min": spec.get("e_min", 0.02),
        "alpha": spec.get("alpha", 0.05),
        "fdr": spec.get("fdr", "none"),
        "equivalence_margin": spec.get("equivalence_margin"),
        "calibration_id": spec.get("calibration_id"),
        "bootstrap_resamples": spec.get("bootstrap_resamples", 10000),
    }
    if doc["purpose"] == "aa-calibration":
        overrides = [json.dumps(a.get("param_overrides", {}), sort_keys=True) for a in arms]
        if len(set(overrides)) != 1 or any(a.get("setup_commands") or a.get("env") or a.get("profile") for a in arms):
            raise ValueError("aa-calibration campaigns require identical arms")
    sealed = seal(doc)
    store.put(sealed)
    return sealed


def run(campaign_id: str, store: Store, log: Any = print, use_nvml: bool = True) -> list[str]:
    cmp = store.get(campaign_id)
    workload = cmp["workload"]
    arms = {a["id"]: a for a in cmp["arms"]}
    hwp = collect_hardware_profile(use_nvml=use_nvml)
    store.put(hwp)
    done = {r["order_index"] for r in store.results_for(campaign_id)}
    models = [resolve(workload["model"]["path"])] if workload.get("model") else []
    fp_cache: dict[str, str] = {}
    layout = load_layout()
    rb = bundle(workload["runtime"]["binary"])
    if workload["runtime"].get("bundle_digest"):
        rb["digest"] = workload["runtime"]["bundle_digest"]

    def fingerprint(arm_id: str) -> str:
        """Captured after the arm's profile/hooks are in place, so it records the measured state."""
        arm = arms[arm_id]
        if arm_id not in fp_cache or arm.get("setup_commands") or arm.get("profile"):
            fp = collect_fingerprint(
                hwp["id"], models=models, use_nvml=use_nvml, runtime_bundles=[rb], platform_state=platform_info(layout)
            )
            store.put(fp)
            fp_cache[arm_id] = fp["id"]
        return fp_cache[arm_id]

    def arm_context(arm: Doc) -> contextlib.AbstractContextManager[object]:
        prof = arm.get("profile")
        if not prof:
            return contextlib.nullcontext()
        return profile_applied(layout, prof["name"], bool(prof.get("include_experimental")))

    for arm_id, arm in arms.items():  # warmup runs are executed but not stored as results
        wl = apply_overrides(workload, arm.get("param_overrides"))
        for i in range(workload["warmup_runs"]):
            log(f"warmup {arm_id} {i + 1}/{workload['warmup_runs']}")
            with arm_context(arm):
                execute_run(
                    wl,
                    Store(store.root / "warmup"),
                    hwp["id"],
                    fingerprint(arm_id),
                    order_index=-1 - i,
                    seed=cmp["design"]["seed"],
                    env=arm.get("env"),
                    setup_commands=arm.get("setup_commands"),
                )
    ids: list[str] = []
    for slot in cmp["schedule"]:
        if slot["index"] in done:
            continue
        arm = arms[slot["arm"]]
        wl = apply_overrides(workload, arm.get("param_overrides"))
        with arm_context(arm):
            res = execute_run(
                wl,
                store,
                hwp["id"],
                fingerprint(slot["arm"]),
                order_index=slot["index"],
                seed=cmp["design"]["seed"],
                campaign_id=campaign_id,
                arm_id=slot["arm"],
                block=slot["block"],
                env=arm.get("env"),
                setup_commands=arm.get("setup_commands"),
            )
        log(f"[{slot['index'] + 1}/{len(cmp['schedule'])}] arm={slot['arm']} status={res['status']} {res['id'][:16]}")
        ids.append(res["id"])
    return ids


def _fingerprint_differences(store: Store, by_arm: dict[str, list[Doc]]) -> dict[str, Any]:
    """Top-level fingerprint sections that differ between arms (what actually changed)."""
    fps = {arm: store.get(rs[0]["fingerprint_id"]) for arm, rs in by_arm.items() if rs}
    keys = (
        "kernel",
        "os",
        "security",
        "sched",
        "cpufreq",
        "memory_policy",
        "services",
        "accelerator_state",
        "platform",
        "drivers",
        "runtime_bundles",
        "models",
    )
    diff: dict[str, Any] = {}
    for k in keys:
        vals = {arm: json.dumps(fp.get(k), sort_keys=True) for arm, fp in fps.items()}
        if len(set(vals.values())) > 1:
            diff[k] = {arm: json.loads(v) for arm, v in vals.items()}
    return diff


def analyze(campaign_id: str, store: Store) -> Doc:
    cmp = store.get(campaign_id)
    results = store.results_for(campaign_id)
    ok = [r for r in results if r["status"] == "ok"]
    excluded = [
        {"id": r["id"], "status": r["status"], "detail": r.get("status_detail")} for r in results if r["status"] != "ok"
    ]
    warnings: list[str] = []
    kinds = {r["measurement_kind"] for r in ok}
    kind = "mixed" if len(kinds) > 1 else (kinds.pop() if kinds else "hardware")
    if kind != "hardware":
        warnings.append("SIMULATED DATA: this analysis validates the harness; it is not a hardware measurement")
    if len({r["hardware_profile_id"] for r in ok}) > 1:
        warnings.append("results come from more than one hardware profile")
    planned = len(cmp["schedule"])
    if len(results) < planned:
        warnings.append(f"incomplete campaign: {len(results)} of {planned} planned runs present")
    if excluded:
        warnings.append(f"{len(excluded)} run(s) excluded (non-ok status)")
    by_arm: dict[str, list[Doc]] = defaultdict(list)
    for r in ok:
        by_arm[r["arm_id"]].append(r)

    better = {m["name"]: m["better"] for m in cmp["workload"]["metrics"]}
    noise: dict[str, float] = {}
    if cmp.get("calibration_id"):
        cal = store.get(cmp["calibration_id"])
        noise = (cal.get("calibration") or {}).get("noise_floor", {})
        if store.get(cal["campaign_id"])["workload"]["name"] != cmp["workload"]["name"]:
            warnings.append("calibration campaign used a different workload")
        cal_hwp = {store.get(i)["hardware_profile_id"] for i in cal["result_ids"][:1]}
        if ok and cal_hwp and ok[0]["hardware_profile_id"] not in cal_hwp:
            warnings.append("calibration was measured on a different hardware profile")
    elif cmp["purpose"] != "aa-calibration":
        warnings.append("no A/A calibration referenced: decisions use e_min only")

    summaries: dict[str, Any] = {}
    for arm, rs in by_arm.items():
        summaries[arm] = {}
        for m in [cmp["primary_metric"], *cmp["guard_metrics"]]:
            vals = [r["metrics"][m]["value"] for r in rs if m in r["metrics"] and r["metrics"][m]["value"] is not None]
            if vals:
                arr = np.asarray(vals)
                summaries[arm][m] = {
                    "n": len(vals),
                    "median": float(np.median(arr)),
                    "p10": float(np.percentile(arr, 10)),
                    "p90": float(np.percentile(arr, 90)),
                    "cv": float(np.std(arr, ddof=1) / np.mean(arr)) if len(vals) > 1 else None,
                    "unit": rs[0]["metrics"][m]["unit"],
                }
    comps = []
    base = by_arm.get(cmp["baseline_arm"], [])
    for arm in sorted(by_arm):
        if arm == cmp["baseline_arm"]:
            continue
        for role, m in [("primary", cmp["primary_metric"])] + [("guard", g) for g in cmp["guard_metrics"]]:
            a = [r["metrics"][m]["value"] for r in base if m in r["metrics"]]
            b = [r["metrics"][m]["value"] for r in by_arm[arm] if m in r["metrics"]]
            c = compare(
                m,
                a,
                b,
                better[m],
                cmp["alpha"],
                cmp["e_min"],
                noise.get(m),
                cmp["equivalence_margin"],
                cmp["bootstrap_resamples"],
                cmp["design"]["seed"],
            )
            comps.append((arm, role, c))
    if cmp["fdr"] == "bh":
        fdr_bh([c for _, _, c in comps], cmp["alpha"], cmp["equivalence_margin"])
    calibration = None
    if cmp["purpose"] == "aa-calibration":
        pooled_cv = {
            m: [s[m]["cv"] for s in summaries.values() if m in s and s[m]["cv"] is not None]
            for m in [cmp["primary_metric"], *cmp["guard_metrics"]]
        }
        calibration = {
            "noise_floor": {
                c.metric: noise_floor_from_aa(c) for _, role, c in comps if c.verdict != "insufficient-data"
            },
            "run_cv": {m: float(np.mean(v)) for m, v in pooled_cv.items() if v},
            "aa_false_claims": [c.metric for _, _, c in comps if c.verdict in ("improvement", "regression")],
        }
        if calibration["aa_false_claims"]:
            warnings.append("A/A campaign produced a claim: harness or environment not stable enough")
    out = []
    for arm, role, c in comps:
        d = c.as_dict()
        d = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in d.items()}
        d["improvement_ci"] = [None if (x is None or not np.isfinite(x)) else x for x in c.improvement_ci]
        out.append({"arm": arm, "role": role, **d})
    doc: Doc = {
        "schema": "ailx.analysis/v0",
        "campaign_id": campaign_id,
        "created_at": utcnow(),
        "purpose": cmp["purpose"],
        "measurement_kind": kind,
        "result_ids": [r["id"] for r in ok],
        "excluded": excluded,
        "arm_summaries": summaries,
        "comparisons": out,
        "calibration": calibration,
        "fingerprint_differences": _fingerprint_differences(store, by_arm),
        "warnings": warnings,
        "decision_rule": DECISION_RULE,
        "harness_version": __version__,
    }
    sealed = seal(doc)
    store.put(sealed)
    return sealed
