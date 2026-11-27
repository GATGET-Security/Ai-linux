# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ailx.telemetry.supervisor import Supervisor, energy_summary, summarize

REPO = Path(__file__).resolve().parents[2]


def test_counter_wrap_and_gauge_window() -> None:
    metrics = [
        {"name": "e", "unit": "uJ", "type": "counter", "wrap": 100.0},
        {"name": "p", "unit": "W", "type": "gauge"},
    ]
    samples = [
        {"t_ns": t, "values": {"e": e, "p": p}}
        for t, e, p in [(0, 90.0, 1.0), (10, 95.0, 2.0), (20, 5.0, 3.0), (30, 15.0, 4.0), (40, 20.0, 5.0)]
    ]
    s = summarize(metrics, samples, (5, 35))
    assert s["e"]["delta"] == pytest.approx(30.0)  # anchors t=0 and t=40: 90→95→(wrap)105→115→120
    assert s["p"]["mean"] == pytest.approx(3.0)


def test_supervisor_builtin_and_unavailable_samplers() -> None:
    sup = Supervisor(["psi", "nvml", "rapl"], interval=0.05)
    os.environ["AILX_RAPL_SOCKET"] = "/nonexistent/rapl.sock"
    sup.start(settle_s=0.5)
    sup.mark("measure", "start")
    time.sleep(0.3)
    sup.mark("measure", "end")
    sup.stop()
    summ = sup.summary()
    assert summ["rapl"]["available"] is False and "not reachable" in summ["rapl"]["reason"]
    if Path("/proc/pressure/cpu").exists():
        assert summ["psi"]["available"] is True
    assert sup.failed() == []


def test_external_sampler_dying_early_is_detected(tmp_path: Path) -> None:
    script = tmp_path / "dies.py"
    script.write_text(
        "import json,time\n"
        "print(json.dumps({'schema':'ailx.sample-stream/v0','kind':'header','sampler':'x','version':'1',"
        "'clock':'CLOCK_MONOTONIC','available':True,'metrics':[]}), flush=True)\n"
        "time.sleep(0.1)\n"
    )
    sup = Supervisor([f"exec:{sys.executable} {script}"])
    sup.start(settle_s=0.6)
    sup.stop()
    assert sup.failed() == [f"exec:{sys.executable} {script}"]


def test_energy_summary_marks_simulated_meter() -> None:
    tele = {"meter_fake": {"available": True, "simulated": True, "metrics": {"wall.energy_j": {"delta": 10.0}}}}
    e = energy_summary(tele)
    assert e["wall_j"] == 10.0 and e["wall_simulated"] is True and e["ground_truth"] == "none"


def _load_collector():  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location("coll", REPO / "helpers/rapl-collector/ailx_rapl_collector.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_rapl_collector_access_policy() -> None:
    coll = _load_collector()
    lookup = {"alice": [100, 2000]}
    assert coll.is_allowed(0, 0, 2000, None) is True
    assert coll.is_allowed(1000, 2000, 2000, None) is True
    assert coll.is_allowed(1000, 100, 2000, lambda uid, gid: lookup["alice"]) is True
    assert coll.is_allowed(1001, 100, 2000, lambda uid, gid: [100]) is False


def test_rapl_collector_serves_snapshots(tmp_path: Path) -> None:
    pc = tmp_path / "powercap"
    for zone, name, val in (("intel-rapl:0", "package-0", "1000"), ("intel-rapl:0:0", "core", "500")):
        (pc / zone).mkdir(parents=True)
        (pc / zone / "name").write_text(name)
        (pc / zone / "energy_uj").write_text(val)
        (pc / zone / "max_energy_range_uj").write_text("262143328850")
    sock = tmp_path / "rapl.sock"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "helpers/rapl-collector/ailx_rapl_collector.py"),
            "--socket",
            str(sock),
            "--powercap",
            str(pc),
            "--group",
            str(os.getgid()),
            "--min-interval",
            "0.05",
        ]
    )
    try:
        for _ in range(50):
            if sock.exists():
                break
            time.sleep(0.05)
        from ailx.telemetry.samplers.rapl import query

        snap = query(str(sock))
        assert snap["domains"] == {"package-0": 1000}  # sub-zones (core) are not exposed
        assert oct(sock.stat().st_mode & 0o777) == "0o660"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
