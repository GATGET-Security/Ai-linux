# SPDX-License-Identifier: MIT
"""``ailx`` CLI end-to-end on a fake host, plus campaign integration (profile arms)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ailx import campaign
from ailx.store import Store
from ailx.system.cli import main

REPO = Path(__file__).resolve().parents[2]


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    rc = main(list(args))
    return rc, capsys.readouterr().out


def test_inventory_commands(host: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = run(capsys, "--no-nvml", "--json", "status")
    st = json.loads(out)
    assert rc == 0 and st["platform"]["active_profile"] is None and st["mode"] in ("system", "user")
    rc, out = run(capsys, "--no-nvml", "--json", "resources")
    r = json.loads(out)
    assert r["memory"]["available_bytes"] == 60000000 * 1024 and r["cpu"]["logical_cpus"] == 4
    rc, out = run(capsys, "--no-nvml", "--json", "doctor")
    ids = {c["id"]: c["status"] for c in json.loads(out)}
    assert rc == 0 and ids["transactions"] == "ok" and ids["nvidia-driver"] == "skip" and ids["host-os"] == "ok"
    rc, out = run(capsys, "--no-nvml", "hardware")
    assert rc == 0 and "Test CPU" in out


def test_profile_apply_requires_yes_then_rollback(host: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = run(capsys, "profile", "apply", "inference", "--include-experimental")
    assert rc == 2 and "--yes" in out and (host / "proc/sys/vm/swappiness").read_text().strip() == "60"
    rc, out = run(capsys, "profile", "apply", "inference", "--include-experimental", "--dry-run")
    assert rc == 0 and "dry run" in out
    rc, out = run(capsys, "profile", "apply", "inference", "--include-experimental", "--yes")
    assert rc == 0 and "committed" in out and (host / "proc/sys/vm/swappiness").read_text() == "10"
    (host / "proc/sys/vm/swappiness").write_text("20")
    rc, out = run(capsys, "--no-nvml", "status")
    assert "DRIFT vm.swappiness" in out
    rc, out = run(capsys, "rollback")
    assert rc == 1 and "rolled-back-partial" in out  # drifted value is not overwritten
    rc, out = run(capsys, "history")
    assert out.count("txn-") == 1


def test_errors_are_reported_not_raised(host: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "plan", "does-not-exist"]) == 1
    assert main(["rollback"]) == 1
    assert main(["engine", "install", "unknown-engine"]) == 1


def test_campaign_arm_with_profile_is_applied_and_rolled_back(host: Path, tmp_path: Path) -> None:
    spec = json.loads((REPO / "workloads/campaigns/synthetic-effect-5pct.json").read_text())
    spec["workload"] = str(REPO / "workloads/smoke/synthetic-simulated.json")
    spec["arms"] = [
        {"id": "stock", "description": "baseline"},
        {
            "id": "inference",
            "description": "AI Linux inference profile (experimental)",
            "profile": {"name": "inference", "include_experimental": True},
        },
    ]
    spec["baseline_arm"] = "stock"
    spec["design"] = {"blocks": 2, "runs_per_block": 2, "seed": 5}
    spec["bootstrap_resamples"] = 300
    st = Store(tmp_path / "store")
    cmp = campaign.plan(spec, st)
    campaign.run(cmp["id"], st, log=lambda *_: None, use_nvml=False)
    assert (host / "proc/sys/vm/swappiness").read_text().strip() == "60"  # rolled back after every run
    by_arm: dict[str, set[str]] = {}
    for r in st.results_for(cmp["id"]):
        by_arm.setdefault(r["arm_id"], set()).add(r["fingerprint_id"])
    fp_stock = st.get(next(iter(by_arm["stock"])))
    fp_inf = st.get(next(iter(by_arm["inference"])))
    assert fp_stock["platform"]["active_profile"] is None
    assert fp_inf["platform"]["active_profile"] == "inference"
    assert fp_inf["platform"]["applied"]["vm.swappiness@/proc/sys/vm/swappiness"] == "10"
    ana = campaign.analyze(cmp["id"], st)
    assert "platform" in ana["fingerprint_differences"]
