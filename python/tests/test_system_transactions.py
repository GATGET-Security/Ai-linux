# SPDX-License-Identifier: MIT
"""Profiles, plans and transactions on a writable fake host root (real file writes)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ailx.system import knobs, profiles
from ailx.system.executor import DirectExecutor, ExecutorError
from ailx.system.paths import load_layout
from ailx.system.profiles import ProfileError
from ailx.system.transactions import Manager, TransactionError


def val(root: Path, rel: str) -> str:
    return (root / rel.lstrip("/")).read_text().strip()


THP = "/sys/kernel/mm/transparent_hugepage/enabled"
SWAP = "/proc/sys/vm/swappiness"
EPP0 = "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"


def test_registry_is_schema_valid_and_classified() -> None:
    reg = knobs.registry()
    assert reg["kernel.cmdline.mitigations_off"]["security_class"] == "forbidden"
    assert all(k["validation_status"] != "hw-validated" for k in reg.values())  # nothing validated yet


def test_experimental_settings_are_inactive_by_default(host: Path) -> None:
    lay = load_layout()
    for name, prof in profiles.load_all(lay).items():
        p = profiles.plan(lay, prof)
        assert p.changes == [], name
        assert all(s.action == "skip" and "experimental" in s.reason for s in p.steps)


def test_plan_with_experimental(host: Path) -> None:
    lay = load_layout()
    p = profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True)
    changes = {(s.knob, s.target): (s.current, s.desired) for s in p.changes}
    assert changes[("kernel.mm.thp.enabled", THP)] == ("madvise", "always")
    assert changes[("vm.swappiness", SWAP)] == ("60", "10")
    assert sum(1 for k, _ in changes if k == "cpu.cpufreq.epp") == 4  # one change per CPU
    assert p.warnings


def test_forbidden_knob_cannot_be_in_a_profile(host: Path) -> None:
    d = host / "etc/ailx/profiles"
    d.mkdir(parents=True)
    (d / "bad.toml").write_text(
        'schema = "ailx.profile/v0"\nname = "bad"\ndescription = "x"\n'
        '[[settings]]\nknob = "kernel.cmdline.mitigations_off"\nvalue = "1"\n'
        'experimental = true\nrationale = "no"\n'
    )
    with pytest.raises(ProfileError, match="forbidden"):
        profiles.load_all(load_layout())


def test_conflicting_service_skips_knob(host: Path) -> None:
    lay = load_layout()
    p = profiles.plan(
        lay, profiles.load(lay, "inference"), include_experimental=True, services={"power-profiles-daemon"}
    )
    epp = [s for s in p.steps if s.knob == "cpu.cpufreq.epp"]
    assert epp and all(s.action == "skip" and "power-profiles-daemon" in s.reason for s in epp)


def test_kernel_choice_validation(host: Path) -> None:
    (host / THP.lstrip("/")).write_text("[madvise] never\n")  # kernel without 'always'
    lay = load_layout()
    p = profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True)
    thp = next(s for s in p.steps if s.knob == "kernel.mm.thp.enabled")
    assert thp.action == "skip" and "does not offer" in thp.reason


def test_apply_verify_and_rollback(host: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay)
    txn = mgr.apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True))
    assert txn["status"] == "committed" and all(c["verified"] for c in txn["changes"])
    assert val(host, SWAP) == "10" and val(host, EPP0) == "performance"
    assert mgr.state()["active_profile"] == "inference"
    with pytest.raises(TransactionError, match="active"):
        mgr.apply(profiles.plan(lay, profiles.load(lay, "training"), include_experimental=True))
    t2 = mgr.apply(profiles.plan(lay, profiles.load(lay, "training"), include_experimental=True), replace=True)
    assert mgr.load(txn["id"])["status"] == "rolled-back" and t2["status"] == "committed"
    assert val(host, EPP0) == "balance_performance"  # training's EPP value
    mgr.rollback()
    assert val(host, SWAP) == "60" and val(host, EPP0) == "balance_performance"
    assert "[madvise]" in val(host, THP) or val(host, THP) == "madvise"
    assert mgr.state()["active_txn"] is None


class FailingExecutor(DirectExecutor):
    def __init__(self, layout, fail_at: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(layout)
        self.n, self.fail_at = 0, fail_at

    def write(self, target: str, value: str) -> None:
        self.n += 1
        if self.n == self.fail_at:
            raise ExecutorError("simulated write failure")
        super().write(target, value)


def test_failure_mid_apply_rolls_back(host: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay, FailingExecutor(lay, fail_at=3))
    with pytest.raises(TransactionError, match="rolled back"):
        mgr.apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True))
    assert val(host, SWAP) == "60" and val(host, EPP0) == "balance_performance"
    txn = mgr.all()[-1]
    assert txn["status"] == "failed-rolled-back" and mgr.state()["active_txn"] is None


class LyingExecutor(DirectExecutor):
    def write(self, target: str, value: str) -> None:
        super().write(target, "5" if target.endswith("swappiness") and value == "10" else value)


def test_verification_failure_is_detected(host: Path) -> None:
    lay = load_layout()
    with pytest.raises(TransactionError, match="verification failed"):
        Manager(lay, LyingExecutor(lay)).apply(
            profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True)
        )
    assert val(host, SWAP) == "60"


def test_drift_is_reported_and_not_overwritten(host: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay)
    mgr.apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True))
    (host / SWAP.lstrip("/")).write_text("33\n")  # another tool changes a value we applied
    assert [d["target"] for d in mgr.drift()] == [SWAP]
    t = mgr.rollback()
    assert t["status"] == "rolled-back-partial"
    assert val(host, SWAP) == "33" and val(host, EPP0) == "balance_performance"


def test_dead_man_rollback(host: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay)
    txn = mgr.apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True), confirm_timeout=1.0)
    assert txn["status"] == "pending-confirmation" and val(host, SWAP) == "10"
    for _ in range(60):
        if mgr.load(txn["id"])["status"] != "pending-confirmation":
            break
        time.sleep(0.2)
    assert mgr.load(txn["id"])["status"] == "rolled-back"
    assert val(host, SWAP) == "60"


def test_confirm_keeps_changes(host: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay)
    txn = mgr.apply(
        profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True),
        confirm_timeout=30.0,
        spawn_watchdog=False,
    )
    assert mgr.confirm()["status"] == "committed"
    assert mgr.watchdog_expire(txn["id"]) == "committed" and val(host, SWAP) == "10"


def test_crash_mid_apply_is_recovered(host: Path) -> None:
    """A real process dies (os._exit) after its second write; the journal allows recovery."""
    code = (
        "import os\n"
        "from ailx.system import profiles\n"
        "from ailx.system.executor import DirectExecutor\n"
        "from ailx.system.paths import load_layout\n"
        "from ailx.system.transactions import Manager\n"
        "lay = load_layout()\n"
        "class Crash(DirectExecutor):\n"
        "    n = 0\n"
        "    def write(self, t, v):\n"
        "        super().write(t, v)\n"
        "        Crash.n += 1\n"
        "        if Crash.n == 2: os._exit(9)\n"
        "plan = profiles.plan(lay, profiles.load(lay, 'inference'), include_experimental=True)\n"
        "Manager(lay, Crash(lay)).apply(plan)\n"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 9
    lay = load_layout()
    mgr = Manager(lay)
    stuck = mgr.interrupted()
    assert len(stuck) == 1 and stuck[0]["status"] == "applying"
    with pytest.raises(TransactionError, match="interrupted"):
        mgr.apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True))
    rec = mgr.recover()
    assert rec[0]["status"] == "recovered"
    assert val(host, EPP0) == "balance_performance" and val(host, SWAP) == "60"
    assert "[madvise]" in val(host, THP) or val(host, THP) == "madvise"


def test_state_lock(host: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay)
    with mgr.lock(), pytest.raises(TransactionError, match="lock"):
        Manager(lay).rollback()


def test_gpu_power_limit_knob_with_fake_nvidia_smi(host: Path, fake_gpu: Path) -> None:
    lay = load_layout()
    mgr = Manager(lay)
    plan = profiles.plan(lay, profiles.load(lay, "energy-efficiency"), include_experimental=True)
    gpu = next(s for s in plan.changes if s.knob == "gpu.nvidia.power_limit_fraction")
    assert (gpu.current, gpu.desired) == ("450", "315")
    mgr.apply(plan)
    assert json.loads(fake_gpu.read_text())["gpus"][0]["limit"] == 315.0
    mgr.rollback()
    assert json.loads(fake_gpu.read_text())["gpus"][0]["limit"] == 450.0
