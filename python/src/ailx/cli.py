# SPDX-License-Identifier: MIT
"""``ailx-bench`` command line (WP8)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ailx import __version__, campaign
from ailx.core import Doc, ValidationError, seal, utcnow, validate
from ailx.probe import collect_fingerprint, collect_hardware_profile
from ailx.report import render, validate_claims
from ailx.runner import execute_run
from ailx.stats import power_n
from ailx.store import Store
from ailx.telemetry.supervisor import Supervisor
from ailx.workloads import load_workload


def _store(args: argparse.Namespace) -> Store:
    return Store(args.store or os.environ.get("AILX_STORE", "ailx-store"))


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_probe(a: argparse.Namespace) -> int:
    hwp = collect_hardware_profile(use_nvml=not a.no_nvml)
    fp = collect_fingerprint(hwp["id"], use_nvml=not a.no_nvml)
    if a.store or os.environ.get("AILX_STORE"):
        st = _store(a)
        st.put(hwp)
        st.put(fp)
    out = {"hardware_profile": hwp, "environment_fingerprint": fp}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=2))
        print(f"wrote {a.out}  hwp={hwp['id']}  env={fp['id']}")
    else:
        _print(out)
    unavailable = {**hwp["unavailable"], **fp["unavailable"]}
    if unavailable:
        print(f"\n{len(unavailable)} field(s) unavailable (see 'unavailable' sections):", file=sys.stderr)
        for k, v in unavailable.items():
            print(f"  - {k}: {v}", file=sys.stderr)
    return 0


def cmd_schema_validate(a: argparse.Namespace) -> int:
    rc = 0
    for f in a.files:
        try:
            validate(json.loads(Path(f).read_text()), a.schema)
            print(f"OK   {f}")
        except (ValidationError, ValueError) as exc:
            print(f"FAIL {f}: {exc}")
            rc = 1
    return rc


def cmd_run(a: argparse.Namespace) -> int:
    st = _store(a)
    wl = load_workload(a.workload)
    hwp = collect_hardware_profile(use_nvml=not a.no_nvml)
    st.put(hwp)
    fp = collect_fingerprint(hwp["id"], models=[wl["model"]["path"]] if wl.get("model") else [], use_nvml=not a.no_nvml)
    st.put(fp)
    res = execute_run(wl, st, hwp["id"], fp["id"], seed=a.seed)
    _print({k: res[k] for k in ("id", "status", "status_detail", "measurement_kind", "metrics", "energy")})
    return 0 if res["status"] == "ok" else 1


def cmd_campaign_plan(a: argparse.Namespace) -> int:
    spec = json.loads(Path(a.spec).read_text())
    cmp = campaign.plan(spec, _store(a), base_dir=Path(a.spec).resolve().parent, calibration_id=a.calibration)
    print(cmp["id"])
    return 0


def cmd_campaign_run(a: argparse.Namespace) -> int:
    campaign.run(a.campaign_id, _store(a), use_nvml=not a.no_nvml)
    return 0


def _analyze_and_report(st: Store, campaign_id: str, report: str | None) -> Doc:
    ana = campaign.analyze(campaign_id, st)
    text = render(st, ana)
    if report:
        Path(report).write_text(text)
        print(f"report: {report}")
    print(ana["id"])
    for c in ana["comparisons"]:
        print(f"  {c['arm']:>12} {c['role']:<7} {c['metric']:<22} {c['verdict']}")
    for w in ana["warnings"]:
        print(f"  WARNING: {w}")
    if ana.get("calibration"):
        print(f"  noise floor: {ana['calibration']['noise_floor']}")
    return ana


def cmd_campaign_analyze(a: argparse.Namespace) -> int:
    _analyze_and_report(_store(a), a.campaign_id, a.report)
    return 0


def cmd_campaign_all(a: argparse.Namespace) -> int:
    st = _store(a)
    spec = json.loads(Path(a.spec).read_text())
    cmp = campaign.plan(spec, st, base_dir=Path(a.spec).resolve().parent, calibration_id=a.calibration)
    print(f"campaign {cmp['id']} ({len(cmp['schedule'])} runs)")
    campaign.run(cmp["id"], st, use_nvml=not a.no_nvml)
    _analyze_and_report(st, cmp["id"], a.report)
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    st = _store(a)
    text = render(st, st.get(a.analysis_id))
    if a.out:
        Path(a.out).write_text(text)
    else:
        print(text)
    return 0


def cmd_store_verify(a: argparse.Namespace) -> int:
    problems = _store(a).verify()
    for p in problems:
        print(f"PROBLEM {p}")
    print("store OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_store_list(a: argparse.Namespace) -> int:
    for doc_id, schema in _store(a).list_docs(a.schema):
        print(f"{doc_id}  {schema}")
    return 0


def cmd_power(a: argparse.Namespace) -> int:
    _print(power_n(a.cv, a.effect, a.e_min, a.alpha, a.power, a.noise_floor, a.sims))
    return 0


def cmd_quality_reference(a: argparse.Namespace) -> int:
    from ailx.quality.kld import make_reference

    meta = make_reference(a.binary, a.model, a.corpus, a.out_dir, a.ctx, a.chunks, a.threads)
    print(f"reference: {Path(a.out_dir) / 'reference.json'}")
    _print(meta["aa"])
    return 0


def cmd_quality_evaluate(a: argparse.Namespace) -> int:
    from ailx.quality.kld import evaluate

    st = _store(a)
    q = evaluate(a.binary, a.candidate, a.reference, a.threads)
    hwp = collect_hardware_profile(use_nvml=not a.no_nvml)
    st.put(hwp)
    fp = collect_fingerprint(hwp["id"], models=[a.candidate], use_nvml=not a.no_nvml)
    st.put(fp)
    metrics = {
        k: {"value": v["value"], "unit": "%" if k in ("same_top_p", "rms_dp") else "nats" if "kld" in k else ""}
        for k, v in q["metrics"].items()
    }
    doc = seal(
        {
            "schema": "ailx.result/v0",
            "campaign_id": None,
            "arm_id": None,
            "block": None,
            "order_index": None,
            "workload_name": "quality-kld",
            "workload_sha256": json.loads(Path(a.reference).read_text())["kld_file"]["sha256"],
            "fingerprint_id": fp["id"],
            "hardware_profile_id": hwp["id"],
            "measurement_kind": "hardware",
            "started_at": utcnow(),
            "duration_s": 0.0,
            "status": "ok"
            if q["verdict"] in ("no-detectable-quality-change", "quality-change-detected")
            else "invalid",
            "status_detail": q["detail"],
            "metrics": metrics,
            "tool": {"name": "llama-perplexity", "version": None, "command": []},
            "telemetry": {},
            "energy": {},
            "stability_events": [],
            "quality": q,
        }
    )
    st.put(doc)
    print(doc["id"])
    print(f"verdict: {q['verdict']} — {q['detail']}")
    for c in q["prerequisites"]:
        print(f"  [{'ok' if c['ok'] else ('??' if c['ok'] is None else 'FAIL')}] {c['check']}: {c['detail']}")
    return 0 if q["verdict"] == "no-detectable-quality-change" else 2


def cmd_validate_claims(a: argparse.Namespace) -> int:
    rc = 0
    for f in a.files:
        for no, line in validate_claims(Path(f).read_text()):
            print(f"{f}:{no}: number without stored ID: {line[:120]}")
            rc = 1
    return rc


def cmd_telemetry_check(a: argparse.Namespace) -> int:
    sup = Supervisor(a.samplers)
    sup.start()
    sup.mark("measure", "start")
    time.sleep(a.seconds)
    sup.mark("measure", "end")
    sup.stop()
    _print(sup.summary())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ailx-bench", description="AI Linux measurement foundation")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--store", help="result store directory (default: $AILX_STORE or ./ailx-store)")
    p.add_argument("--no-nvml", action="store_true", help="do not use NVML even if available")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("probe", help="collect hardware profile and environment fingerprint")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_probe)

    g = sub.add_parser("schema", help="schema utilities").add_subparsers(dest="sub", required=True)
    v = g.add_parser("validate")
    v.add_argument("files", nargs="+")
    v.add_argument("--schema", help="schema id; default: the document's 'schema' field")
    v.set_defaults(fn=cmd_schema_validate)

    s = sub.add_parser("run", help="single run of a workload manifest")
    s.add_argument("workload")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_run)

    c = sub.add_parser("campaign", help="pre-registered A/B campaigns").add_subparsers(dest="sub", required=True)
    x = c.add_parser("plan")
    x.add_argument("spec")
    x.add_argument("--calibration", help="analysis ID of an A/A calibration campaign")
    x.set_defaults(fn=cmd_campaign_plan)
    x = c.add_parser("run")
    x.add_argument("campaign_id")
    x.set_defaults(fn=cmd_campaign_run)
    x = c.add_parser("analyze")
    x.add_argument("campaign_id")
    x.add_argument("--report")
    x.set_defaults(fn=cmd_campaign_analyze)
    x = c.add_parser("all", help="plan + run + analyze")
    x.add_argument("spec")
    x.add_argument("--calibration", help="analysis ID of an A/A calibration campaign")
    x.add_argument("--report")
    x.set_defaults(fn=cmd_campaign_all)

    s = sub.add_parser("report")
    s.add_argument("analysis_id")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_report)

    g = sub.add_parser("store").add_subparsers(dest="sub", required=True)
    g.add_parser("verify").set_defaults(fn=cmd_store_verify)
    x = g.add_parser("list")
    x.add_argument("--schema")
    x.set_defaults(fn=cmd_store_list)

    s = sub.add_parser("power", help="simulation-based runs-per-arm estimate")
    s.add_argument("--cv", type=float, required=True, help="run-level coefficient of variation (from A/A)")
    s.add_argument("--effect", type=float, required=True, help="true effect to detect, e.g. 0.05")
    s.add_argument("--e-min", type=float, default=0.02)
    s.add_argument("--alpha", type=float, default=0.05)
    s.add_argument("--power", type=float, default=0.8)
    s.add_argument("--noise-floor", type=float)
    s.add_argument("--sims", type=int, default=200)
    s.set_defaults(fn=cmd_power)

    q = sub.add_parser("quality", help="KLD quality gate").add_subparsers(dest="sub", required=True)
    x = q.add_parser("reference")
    for arg in ("--binary", "--model", "--corpus", "--out-dir"):
        x.add_argument(arg, required=True)
    x.add_argument("--ctx", type=int, default=512)
    x.add_argument("--chunks", type=int, default=32)
    x.add_argument("--threads", type=int)
    x.set_defaults(fn=cmd_quality_reference)
    x = q.add_parser("evaluate")
    for arg in ("--binary", "--candidate", "--reference"):
        x.add_argument(arg, required=True)
    x.add_argument("--threads", type=int)
    x.set_defaults(fn=cmd_quality_evaluate)

    s = sub.add_parser("validate-claims", help="flag performance numbers without stored IDs")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_validate_claims)

    g = sub.add_parser("telemetry", help="sampler checks").add_subparsers(dest="sub", required=True)
    x = g.add_parser("check")
    x.add_argument("--samplers", nargs="+", default=["psi", "cpu", "nvml", "rapl"])
    x.add_argument("--seconds", type=float, default=2.0)
    x.set_defaults(fn=cmd_telemetry_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
