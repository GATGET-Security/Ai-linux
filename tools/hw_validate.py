#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Automated, reproducible hardware validation run for the AI Compute Engine (Phase 1.5).

Target: the reference workstation (Ryzen 9 7950X3D + RTX 4090, Ubuntu 26.04), see
docs/runbooks/first-hardware-run.md §8. The same script runs on any machine; on anything else the
reference expectations simply show deviations.

Steps (each recorded with status pass / fail / deviation / skipped and the evidence IDs):

  inventory     capabilities + doctor, saved as JSON
  expectations  capabilities vs the reference expectations (workloads/hw/reference-7950x3d-4090.json)
  engine        llama.cpp engine integrity (manifest + artifact hashes)
  models        model analysis + placement plan per model
  runtime       managed llama-server per model: health, one completion, RSS/VRAM vs prediction (H-R-11)
  admission     a second instance that exceeds the budget must be refused before it starts
  calibrate     A/A calibration on the tuning workload (noise floor; no false claims allowed)
  tune          screening -> confirmation -> adoption per model, with the calibration (H-R-12)

Nothing here marks a ledger row as validated: the report lists *suggested* ledger updates for a
maintainer to review. ``--label`` names the platform under test (e.g. ``ubuntu-26.04-stock`` vs
``ailx-image-hw``) so that the same run feeds the base-distribution bake-off (ADR-0005).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

from ailx import __version__
from ailx.compute.capabilities import collect_capabilities
from ailx.compute.model import analyze
from ailx.compute.planner import Request, plan
from ailx.compute.runtime import AdmissionError, RuntimeManager, StartSpec, _gpu_memory_by_pid
from ailx.compute.tuning import Tuner, TuneSpec
from ailx.core import utcnow
from ailx.system import doctor
from ailx.system.engines import EngineManager
from ailx.system.paths import load_layout

REPO = Path(__file__).resolve().parents[1]
GiB = 1024**3
STEPS = ["inventory", "expectations", "engine", "models", "runtime", "admission", "calibrate", "tune"]
LEDGER = {
    "inventory": "VL-1.5-01 capability engine on the reference workstation",
    "expectations": "VL-1.5-01 capability engine on the reference workstation",
    "runtime": "VL-1.5-02 memory prediction vs measured RSS/VRAM (H-R-11) / VL-1.5-03 managed CUDA llama-server",
    "admission": "VL-1.5-04 admission control refuses over-budget starts on 24 GB VRAM",
    "calibrate": "VL-1.5-05 A/A calibration of the tuning workload",
    "tune": "VL-1.5-05 autotuning decision on reference hardware (H-R-12)",
}


def lookup(caps: dict[str, Any], path: str) -> Any:
    gpu = next((d for d in caps["accelerators"] if d["kind"] == "gpu"), {})
    special = {
        "cpu.llc_domains.length": len(caps["cpu"]["llc_domains"]),
        "cpu.llc_domains.l3_mib": sorted(round((d["l3_bytes"] or 0) / 2**20) for d in caps["cpu"]["llc_domains"]),
        "memory.total_gib": (caps["memory"]["total_bytes"] or 0) / GiB,
        "gpu.name": gpu.get("name"),
        "gpu.total_gib": (gpu.get("memory", {}).get("total_bytes") or 0) / GiB,
        "gpu.compute_capability": gpu.get("compute_capability"),
        "gpu.host_link.max_gen": (gpu.get("host_link") or {}).get("max_gen"),
        "gpu.host_link.max_width": (gpu.get("host_link") or {}).get("max_width"),
        "gpu.bandwidth_gbs": ((gpu.get("memory", {}).get("bandwidth") or {}).get("bytes_per_s") or 0) / 1e9,
        "backend.cuda.usable": next((b["usable"] for b in caps["backends"] if b["backend"] == "cuda"), False),
    }
    if path in special:
        return special[path]
    cur: Any = caps
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def check(exp: dict[str, Any], actual: Any) -> bool:
    op, v = exp["op"], exp["value"]
    if op == "==":
        return bool(actual == v)
    if op == "set==":
        return sorted(actual or []) == sorted(v)
    if op == "contains":
        return set(v) <= set(actual or [])
    if op == "between":
        return actual is not None and v[0] <= float(actual) <= v[1]
    raise ValueError(op)


class Run:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.lay = load_layout()
        self.report: dict[str, Any] = {
            "schema": "ailx.hw-validation-report/v0",
            "started_at": utcnow(),
            "label": a.label,
            "ailx_version": __version__,
            "host": platform.node(),
            "os_release": _os_release(),
            "kernel": platform.release(),
            "models": a.model,
            "steps": [],
            "note": "Suggested ledger updates require maintainer review; nothing is marked validated automatically.",
        }
        self.caps: dict[str, Any] = {}
        self.analyses: dict[str, dict[str, Any]] = {}
        self.calibration: dict[str, str] = {}
        self.reference_match = False

    def save(self, name: str, doc: Any) -> str:
        p = self.out / name
        p.write_text(json.dumps(doc, indent=2, default=str))
        return str(p)

    def step(self, name: str, fn: Any) -> None:
        t0 = time.time()
        print(f"== {name}", flush=True)
        try:
            status, detail, evidence = fn()
        except Exception as exc:  # a failing step is recorded; later steps still run where possible
            status, detail, evidence = "fail", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()}
        rec = {
            "step": name,
            "status": status,
            "detail": detail,
            "evidence": evidence,
            "seconds": round(time.time() - t0, 1),
        }
        if name in LEDGER and name != "expectations":
            if not self.reference_match:
                verdict = "no update (machine deviates from the reference profile)"
            elif status == "skipped":
                verdict = "not exercised"
            else:
                verdict = "candidate for hw-validated" if status == "pass" else "hw-failed / review"
            rec["ledger_suggestion"] = f"{LEDGER[name]}: {verdict}"
        self.report["steps"].append(rec)
        print(f"   {status}: {detail}", flush=True)
        self.save("report.json", self.report)

    # ------------------------------------------------------------ steps
    def inventory(self) -> tuple[str, str, dict[str, Any]]:
        self.caps = collect_capabilities(self.lay)
        checks = doctor.as_dicts(doctor.run(self.lay))
        fails = [c["id"] for c in checks if c["status"] == "fail"]
        ev = {"capabilities": self.save("capabilities.json", self.caps), "doctor": self.save("doctor.json", checks)}
        ev["capabilities_id"] = self.caps["id"]
        return ("fail" if fails else "pass"), (f"doctor failures: {fails}" if fails else "doctor: no failures"), ev

    def expectations(self) -> tuple[str, str, dict[str, Any]]:
        ref = json.loads(Path(self.a.reference).read_text())
        rows = []
        for e in ref["expectations"]:
            actual = lookup(self.caps, e["path"])
            rows.append({**e, "actual": actual, "ok": check(e, actual)})
        bad = [r for r in rows if not r["ok"]]
        self.reference_match = not bad
        detail = f"{len(rows) - len(bad)}/{len(rows)} expectations met" + (
            "; deviations: " + ", ".join(f"{r['path']}={r['actual']!r}" for r in bad) if bad else ""
        )
        return ("deviation" if bad else "pass"), detail, {"rows": rows}

    def engine(self) -> tuple[str, str, dict[str, Any]]:
        em = EngineManager(self.lay)
        cur = em.current("llama.cpp")
        if not cur:
            return "fail", "no llama.cpp engine installed (ailx engine install llama.cpp --variant cuda)", {}
        v = em.verify("llama.cpp")
        ok = not v.get("problems")
        detail = f"{cur['install_id']} ({cur.get('variant')}): " + (
            "integrity ok" if ok else f"problems {v['problems']}"
        )
        return ("pass" if ok else "fail"), detail, {"manifest": cur, "verify": v}

    def models(self) -> tuple[str, str, dict[str, Any]]:
        ev = {}
        infeasible = []
        for m in self.a.model:
            ana = analyze(m)
            self.analyses[m] = ana
            p = plan(self.caps, ana, Request(ctx=self.a.ctx))
            ev[m] = {"analysis": self.save(f"model-{ana['id'][4:16]}.json", ana), "plan": p}
            if not p["feasible"]:
                infeasible.append(m)
        modes = {Path(m).name: ev[m]["plan"]["mode"] for m in ev}
        return ("fail" if infeasible else "pass"), f"placements {modes}", ev

    def runtime(self) -> tuple[str, str, dict[str, Any]]:
        rm = RuntimeManager(self.lay)
        ev: dict[str, Any] = {}
        failed = []
        for i, m in enumerate(self.a.model):
            name = f"hwv-{i}"
            rec = rm.start(StartSpec(name=name, model=m, ctx=self.a.ctx), self.caps)
            try:
                health = rm.wait_ready(name, self.a.load_timeout)
                out: dict[str, Any] = {}
                if health == "ok":
                    body = json.dumps({"prompt": "The capital of France is", "n_predict": 32}).encode()
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{rec['port']}/completion",
                        data=body,
                        headers={"Content-Type": "application/json"},
                    )
                    out = json.loads(urllib.request.urlopen(req, timeout=600).read())
                st = rm.status(name)
                gpu = _gpu_memory_by_pid().get(rec["pid"])
                ev[m] = {
                    "health": health,
                    "tokens_predicted": out.get("tokens_predicted"),
                    "server_timings": out.get("timings"),
                    "predicted": st["plan"],
                    "rss_bytes": st["live"].get("rss_bytes"),
                    "device_bytes": gpu,
                    "rss_vs_prediction": st["live"].get("rss_vs_prediction"),
                    "device_vs_prediction": round(gpu / st["plan"]["device_bytes"], 3)
                    if gpu and st["plan"]["device_bytes"]
                    else None,
                    "log_tail": rm.logs(name, 40),
                }
                if health != "ok" or out.get("tokens_predicted") != 32:
                    failed.append(m)
            finally:
                rm.stop(name)
                rm.remove(name)
        return (
            ("fail" if failed else "pass"),
            f"served {len(self.a.model) - len(failed)}/{len(self.a.model)} models",
            ev,
        )

    def admission(self) -> tuple[str, str, dict[str, Any]]:
        rm = RuntimeManager(self.lay)
        big = max(self.a.model, key=lambda m: self.analyses[m]["weights"]["total_bytes"])
        rm.start(StartSpec(name="hwv-hold", model=big, ctx=self.a.ctx), self.caps)
        try:
            rm.wait_ready("hwv-hold", self.a.load_timeout)
            caps_now = collect_capabilities(self.lay)
            try:
                d = rm.start(
                    StartSpec(name="hwv-extra", model=big, ctx=self.a.ctx, priority="background"),
                    caps_now,
                    dry_run=True,
                )
                return "skipped", "two instances fit the budget on this machine; refusal path not exercised", d
            except AdmissionError as e:
                return "pass", "second instance refused before start: " + "; ".join(e.decision["reasons"]), e.decision
        finally:
            rm.stop("hwv-hold")
            rm.remove("hwv-hold")

    def calibrate(self) -> tuple[str, str, dict[str, Any]]:
        """A/A on each model's own tuning workload (the noise floor is workload-specific)."""
        t = Tuner(self.lay)
        ev: dict[str, Any] = {}
        false_claims = []
        for m in self.a.model:
            spec = TuneSpec(model=m, objective=self.a.objective)
            c = t.calibrate(self.caps, self.analyses[m], m, spec, blocks=self.a.calibration_blocks)
            self.calibration[m] = c["id"]
            cal = c.get("calibration") or {}
            if cal.get("aa_false_claims"):
                false_claims.append(m)
            ev[m] = {
                "analysis": c["id"],
                "run_cv": cal.get("run_cv"),
                "noise_floor": cal.get("noise_floor"),
                "false_claims": cal.get("aa_false_claims"),
                "warnings": c["warnings"],
            }
        cvs = {Path(m).name: ev[m]["run_cv"] for m in ev}
        return ("fail" if false_claims else "pass"), f"A/A without false claims; run CV {cvs}", ev

    def tune(self) -> tuple[str, str, dict[str, Any]]:
        t = Tuner(self.lay)
        ev = {}
        for m in self.a.model:
            spec = TuneSpec(
                model=m,
                objective=self.a.objective,
                calibration_id=self.calibration.get(m),
                include_experimental=self.a.include_experimental,
            )
            rec = t.run(self.caps, self.analyses[m], m, spec)
            ev[m] = {"key": rec["key"], "status": rec["status"], "decision": rec.get("decision"),
                     "adopted": rec.get("adopted"), "evidence": rec["evidence"]}  # fmt: skip
        statuses = {Path(m).name: ev[m]["status"] for m in ev}
        return "pass", f"decisions {statuses} (adopted or baseline are both valid outcomes)", ev

    def main(self) -> int:
        fns = {s: getattr(self, s) for s in STEPS}
        for s in STEPS:
            if s in self.a.skip:
                self.report["steps"].append({"step": s, "status": "skipped", "detail": "--skip"})
                continue
            self.step(s, fns[s])
        self.report["finished_at"] = utcnow()
        self.save("report.json", self.report)
        (self.out / "report.md").write_text(render(self.report))
        print(f"report: {self.out / 'report.md'}")
        return 1 if any(s["status"] == "fail" for s in self.report["steps"]) else 0


def _os_release() -> dict[str, str]:
    out = {}
    with contextlib.suppress(OSError):
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip('"')
    return {k: out[k] for k in ("ID", "VERSION_ID", "PRETTY_NAME") if k in out}


def render(rep: dict[str, Any]) -> str:
    lines = [
        f"# Hardware validation report — {rep['label']}",
        "",
        f"> {rep['os_release'].get('PRETTY_NAME')} · kernel {rep['kernel']} · ailx {rep['ailx_version']} · "
        f"{rep['started_at']} → {rep.get('finished_at')}",
        "",
        "| Step | Status | Detail |",
        "|------|--------|--------|",
    ]
    for s in rep["steps"]:
        lines.append(f"| {s['step']} | {s['status']} | {str(s.get('detail', '')).replace('|', '/')[:300]} |")
    lines += ["", "## Suggested ledger updates (maintainer review required)", ""]
    lines += [f"- {s['ledger_suggestion']}" for s in rep["steps"] if s.get("ledger_suggestion")]
    lines += ["", rep["note"], ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", action="append", required=True, help="GGUF model (repeatable)")
    ap.add_argument("--out", required=True, help="output directory for report.json / report.md and artefacts")
    ap.add_argument("--label", default="unlabeled", help="platform under test, e.g. ubuntu-26.04-stock")
    ap.add_argument("--reference", default=str(REPO / "workloads/hw/reference-7950x3d-4090.json"))
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--objective", default="decode", choices=["decode", "prefill"])
    ap.add_argument("--calibration-blocks", type=int, default=8)
    ap.add_argument("--load-timeout", type=float, default=900)
    ap.add_argument("--include-experimental", action="store_true", help="also screen CPU placement candidates")
    ap.add_argument("--skip", action="append", default=[], choices=STEPS)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    a = ap.parse_args()
    if a.dry_run:
        print(__doc__)
        print(
            json.dumps({"models": a.model, "label": a.label, "steps": [s for s in STEPS if s not in a.skip]}, indent=2)
        )
        return 0
    return Run(a).main()


if __name__ == "__main__":
    sys.exit(main())
