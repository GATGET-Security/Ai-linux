# SPDX-License-Identifier: MIT
"""Phase 1.4 security review: the journal and state are untrusted input; reboot and GPU restore semantics."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ailx.system import profiles
from ailx.system.executor import DirectExecutor, ExecutorError
from ailx.system.install import _trusted_tree
from ailx.system.paths import load_layout
from ailx.system.transactions import Manager, TransactionError


def apply_inference(lay):  # type: ignore[no-untyped-def]
    return Manager(lay).apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True))


def test_tampered_journal_target_is_refused_on_rollback(host: Path, tmp_path: Path) -> None:
    """A group member edits the journal so that root's rollback would write /etc/passwd."""
    lay = load_layout()
    txn = apply_inference(lay)
    victim = host / "etc/passwd"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("root:x:0:0::/root:/bin/bash\n")
    jpath = lay.state_dir / "transactions" / f"{txn['id']}.json"
    j = json.loads(jpath.read_text())
    j["changes"][0]["target"] = "/etc/passwd"
    j["changes"][0]["after"] = victim.read_text().strip()  # make it look un-drifted
    j["changes"][0]["before"] = "evil"
    jpath.write_text(json.dumps(j))
    t = Manager(lay).rollback()
    assert t["status"] == "rollback-failed"
    assert victim.read_text() == "root:x:0:0::/root:/bin/bash\n"
    assert any("refused by registry validation" in e["msg"] for e in t["events"])


def test_tampered_journal_value_is_refused(host: Path) -> None:
    lay = load_layout()
    txn = apply_inference(lay)
    jpath = lay.state_dir / "transactions" / f"{txn['id']}.json"
    j = json.loads(jpath.read_text())
    sw = next(c for c in j["changes"] if c["knob"] == "vm.swappiness")
    sw["before"] = "9999"
    jpath.write_text(json.dumps(j))
    assert Manager(lay).rollback()["status"] == "rollback-failed"
    assert (host / "proc/sys/vm/swappiness").read_text() == "10"  # not written with an out-of-domain value


def test_invalid_transaction_ids_are_rejected(host: Path) -> None:
    lay = load_layout()
    with pytest.raises(TransactionError, match="invalid transaction id"):
        Manager(lay).load("../../etc/shadow")
    lay.state_dir.mkdir(parents=True, exist_ok=True)
    (lay.state_dir / "state.json").write_text(json.dumps({"active_txn": "../x", "active_profile": "p", "history": []}))
    with pytest.raises(TransactionError, match="invalid transaction id"):
        Manager(lay).state()


def test_reboot_expires_runtime_transaction(host: Path) -> None:
    lay = load_layout()
    txn = apply_inference(lay)
    (host / "proc/sys/kernel/random/boot_id").write_text("99999999-0000-0000-0000-000000000000\n")
    mgr = Manager(lay)
    assert mgr.reconcile() == txn["id"]
    assert mgr.load(txn["id"])["status"] == "expired-by-reboot" and mgr.state()["active_txn"] is None


def test_direct_executor_refuses_symlink_escape(host: Path, tmp_path: Path) -> None:
    lay = load_layout()
    thp = host / "sys/kernel/mm/transparent_hugepage/enabled"
    thp.unlink()
    outside = tmp_path / "secret"
    outside.write_text("keep")
    thp.symlink_to(outside)
    with pytest.raises(ExecutorError, match="outside"):
        DirectExecutor(lay).write("/sys/kernel/mm/transparent_hugepage/enabled", "never")
    assert outside.read_text() == "keep"


def test_gpu_above_default_is_not_managed(host: Path, fake_gpu: Path) -> None:
    d = json.loads(fake_gpu.read_text())
    d["gpus"][0]["limit"] = 500.0  # operator raised it above the 450 W default
    fake_gpu.write_text(json.dumps(d))
    lay = load_layout()
    p = profiles.plan(lay, profiles.load(lay, "energy-efficiency"), include_experimental=True)
    gpu = next(s for s in p.steps if s.knob == "gpu.nvidia.power_limit_fraction")
    assert gpu.action == "skip" and "above the vendor default" in gpu.reason


def test_gpu_restore_returns_original_limit(host: Path, fake_gpu: Path) -> None:
    d = json.loads(fake_gpu.read_text())
    d["gpus"][0]["limit"] = 400.0  # operator had already lowered it
    fake_gpu.write_text(json.dumps(d))
    lay = load_layout()
    mgr = Manager(lay)
    mgr.apply(profiles.plan(lay, profiles.load(lay, "energy-efficiency"), include_experimental=True))
    assert json.loads(fake_gpu.read_text())["gpus"][0]["limit"] == 315.0
    mgr.rollback()
    assert json.loads(fake_gpu.read_text())["gpus"][0]["limit"] == 400.0  # original, not the default


def test_untrusted_trees_get_no_root_symlinks(tmp_path: Path) -> None:
    d = tmp_path / "venv"
    d.mkdir()
    os.chmod(d, 0o777)
    assert not _trusted_tree(d)
