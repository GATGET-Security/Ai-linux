# SPDX-License-Identifier: MIT
"""Markdown reports that only contain numbers traceable to stored IDs (WP7)."""

from __future__ import annotations

import re
import statistics
from typing import Any

from ailx.core import Doc
from ailx.store import Store

ID_RE = re.compile(r"\b(?:res|ana|cmp|env|hwp)-[0-9a-f]{8,64}\b")
CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|×|x\b|tok(?:en)?s?/s|tokens/s|t/s|ms\b|GB/s|W\b|J\b|J/token|s/image|req(?:uests)?/s)",
    re.IGNORECASE,
)


def short(doc_id: str) -> str:
    return doc_id[:16]


def _fmt(x: Any, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{digits}g}"
    return str(x)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def render(store: Store, analysis: Doc) -> str:
    cmp = store.get(analysis["campaign_id"])
    a_ref = f"[{short(analysis['id'])}]"
    c_ref = f"[{short(cmp['id'])}]"
    lines = [f"# Campaign report: {cmp['title']} {c_ref}", ""]
    if analysis["measurement_kind"] != "hardware":
        lines += ["> **SIMULATED / NOT A HARDWARE MEASUREMENT.** This report validates the harness only.", ""]
    lines += [
        f"- Campaign: `{cmp['id']}` · Analysis: `{analysis['id']}` · Harness {analysis['harness_version']}",
        f"- Purpose: **{cmp['purpose']}** · Hypotheses: {', '.join(cmp['hypothesis_ids']) or '—'}",
        f"- Workload: `{cmp['workload']['name']}` v{cmp['workload']['version']} ({cmp['workload']['adapter']}),"
        f" output length policy `{cmp['workload']['output_len_policy']}`",
        f"- Pre-registered plan: primary `{cmp['primary_metric']}`, guards {cmp['guard_metrics'] or '—'},"
        f" e_min {cmp['e_min']}, alpha {cmp['alpha']}, FDR `{cmp['fdr']}`, equivalence margin"
        f" {cmp['equivalence_margin']}, RMIT {cmp['design']['blocks']}×{cmp['design']['runs_per_block']}"
        f" seed {cmp['design']['seed']}, calibration `{cmp['calibration_id'] or 'none'}` {c_ref}",
        f"- Measurement kind: **{analysis['measurement_kind']}** · runs used: {len(analysis['result_ids'])},"
        f" excluded: {len(analysis['excluded'])}",
        "",
    ]
    if analysis["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in analysis["warnings"]] + [""]
    lines += [
        "## Arm summaries",
        "",
        "| Arm | Metric | n | Median | P10 | P90 | CV | Unit | Ref |",
        "|-----|--------|---|--------|-----|-----|----|------|-----|",
    ]
    for arm, metrics in sorted(analysis["arm_summaries"].items()):
        for m, s in metrics.items():
            lines.append(
                f"| {arm} | {m} | {s['n']} | {_fmt(s['median'])} | {_fmt(s['p10'])} | {_fmt(s['p90'])} |"
                f" {_fmt(s['cv'], 3)} | {s['unit']} | {a_ref} |"
            )
    lines += [
        "",
        f"## Comparisons vs baseline `{cmp['baseline_arm']}`",
        "",
        "| Arm | Role | Metric | Improvement | CI | p | p (adj.) | Threshold | Verdict | Ref |",
        "|-----|------|--------|-------------|----|---|----------|-----------|---------|-----|",
    ]
    for c in analysis["comparisons"]:
        ci = c.get("improvement_ci") or [None, None]
        lines.append(
            f"| {c['arm']} | {c['role']} | {c['metric']} | {_pct(c.get('improvement'))} |"
            f" [{_pct(ci[0])}, {_pct(ci[1])}] | {_fmt(c.get('p_value'), 3)} | {_fmt(c.get('p_adjusted'), 3)} |"
            f" {_pct(c.get('threshold'))} | **{c['verdict']}** | {a_ref} |"
        )
    lines += [
        "",
        "Verdicts: `no-detectable-difference` means absence of evidence, **not** equivalence.",
        f"Decision rule: {analysis['decision_rule']}",
        "",
    ]
    if analysis.get("calibration"):
        cal = analysis["calibration"]
        lines += [
            "## A/A calibration",
            "",
            "| Metric | Noise floor (|A/A effect|) | Run-level CV | Ref |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {m} | {_pct(v)} | {_fmt(cal.get('run_cv', {}).get(m), 3)} | {a_ref} |"
            for m, v in cal["noise_floor"].items()
        ] + [""]
    energy = _energy_table(store, analysis)
    if energy:
        lines += [
            "## Energy per run (median)",
            "",
            "| Arm | Source | Median J | Ground truth | Ref |",
            "|---|---|---|---|---|",
            *energy,
            "",
        ]
    if analysis.get("fingerprint_differences"):
        lines += ["## Fingerprint differences between arms", ""]
        for k, v in analysis["fingerprint_differences"].items():
            lines.append(f"- `{k}` differs: " + "; ".join(f"{arm}: `{str(val)[:120]}`" for arm, val in v.items()))
        lines.append("")
    if analysis["excluded"]:
        lines += (
            ["## Excluded runs", ""]
            + [f"- `{e['id']}` {e['status']}: {e.get('detail')}" for e in analysis["excluded"]]
            + [""]
        )
    lines += ["## Result IDs", ""]
    by_arm: dict[str, list[str]] = {}
    for rid in analysis["result_ids"]:
        by_arm.setdefault(store.get(rid)["arm_id"], []).append(rid)
    for arm, ids in sorted(by_arm.items()):
        lines.append(f"- {arm}: " + ", ".join(f"`{i}`" for i in ids))
    return "\n".join(lines) + "\n"


def _energy_table(store: Store, analysis: Doc) -> list[str]:
    rows: dict[tuple[str, str], list[float]] = {}
    truth: dict[str, str] = {}
    for rid in analysis["result_ids"]:
        r = store.get(rid)
        e = r.get("energy") or {}
        truth[r["arm_id"]] = e.get("ground_truth", "none")
        for key in ("gpu_total_j", "wall_j"):
            if isinstance(e.get(key), int | float):
                rows.setdefault((r["arm_id"], key), []).append(float(e[key]))
        for k, v in (e.get("rapl_j") or {}).items():
            rows.setdefault((r["arm_id"], k), []).append(float(v))
    return [
        f"| {arm} | {src} | {_fmt(statistics.median(v))} | {truth.get(arm)} | [{short(analysis['id'])}] |"
        for (arm, src), v in sorted(rows.items())
    ]


def validate_claims(text: str) -> list[tuple[int, str]]:
    """Lines that contain a performance-like number without a stored ID on the same line."""
    bad = []
    for no, line in enumerate(text.splitlines(), 1):
        if CLAIM_RE.search(line) and not ID_RE.search(line):
            bad.append((no, line.strip()))
    return bad
