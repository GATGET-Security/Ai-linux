# SPDX-License-Identifier: MIT
"""The privileged helper: allow-list enforcement and consistency with the knob registry."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ailx.system import knobs, profiles
from ailx.system.executor import ExecutorError, HelperExecutor
from ailx.system.paths import load_layout
from ailx.system.transactions import Manager

HELPER = Path(__file__).resolve().parents[2] / "helpers/apply-helper/ailx_apply_helper.py"


def helper_mod():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("helper", HELPER)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def call(req: dict, root: Path, extra_env: dict | None = None) -> dict:  # type: ignore[type-arg]
    env = {k: v for k, v in os.environ.items() if k not in ("SUDO_UID", "PKEXEC_UID")}
    env.update(AILX_HELPER_TEST_ROOT=str(root), **(extra_env or {}))
    r = subprocess.run([sys.executable, str(HELPER)], input=json.dumps(req), capture_output=True, text=True, env=env)
    return json.loads(r.stdout)


def test_allowlist_covers_registry_exactly() -> None:
    allow = helper_mod().ALLOW
    examples = {"file-glob": lambda g: g.replace("cpu[0-9]*", "cpu12").replace("/*/", "/AMDI0101:00/")}
    for k in knobs.registry().values():
        mech = k["mechanism"]
        if mech["type"] not in ("file", "file-glob"):
            continue
        path = mech["path"] if mech["type"] == "file" else examples["file-glob"](mech["glob"])
        rules = [v for p, v in allow if re.fullmatch(p, path)]
        assert rules, f"helper has no rule for {k['id']} ({path})"
        dom = k["domain"]
        values = dom.get("enum") or [str(x) for x in (dom["int_range"][0], dom["int_range"][1], 37)]
        for v in values:
            assert re.fullmatch(rules[0], v), f"{k['id']}: helper rejects allowed value {v}"
        assert not re.fullmatch(rules[0], "evil")


@pytest.mark.parametrize(
    ("target", "value", "why"),
    [
        ("/etc/shadow", "x", "not in allow-list"),
        ("/sys/kernel/mm/transparent_hugepage/../../../etc/passwd", "always", "normalised"),
        ("/sys/kernel/mm/transparent_hugepage/enabled", "always\nnever", "invalid characters"),
        ("/sys/kernel/mm/transparent_hugepage/enabled", "sometimes", "not allowed"),
        ("/proc/sys/vm/swappiness", "201", "not allowed"),
        ("relative/path", "1", "absolute"),
    ],
)
def test_helper_refuses(host: Path, target: str, value: str, why: str) -> None:
    resp = call({"op": "write", "target": target, "value": value}, host)
    assert not resp["ok"] and why in resp["error"]


def test_helper_rejects_symlink_escape(host: Path, tmp_path: Path) -> None:
    thp = host / "sys/kernel/mm/transparent_hugepage/enabled"
    thp.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    thp.symlink_to(outside)
    resp = call({"op": "write", "target": "/sys/kernel/mm/transparent_hugepage/enabled", "value": "never"}, host)
    assert not resp["ok"] and "outside" in resp["error"] and outside.read_text() == "x"


def test_helper_ignores_test_root_under_sudo(host: Path) -> None:
    mod = helper_mod()
    os.environ["SUDO_UID"] = "1000"
    os.environ["AILX_HELPER_TEST_ROOT"] = str(host)
    try:
        assert mod._root() == "/"
    finally:
        del os.environ["SUDO_UID"], os.environ["AILX_HELPER_TEST_ROOT"]


def test_helper_power_limit_only_reductions(host: Path, fake_gpu: Path) -> None:
    exe = str(next(p for p in os.environ["PATH"].split(":") if p.endswith("fakebin"))) + "/nvidia-smi"
    env = {"AILX_HELPER_TEST_NVIDIA_SMI": exe}
    assert call({"op": "write", "target": "nvidia:0", "value": "315"}, host, env)["ok"]
    assert json.loads(fake_gpu.read_text())["gpus"][0]["limit"] == 315.0
    up = call({"op": "write", "target": "nvidia:0", "value": "500"}, host, env)
    assert not up["ok"] and "raising above default" in up["error"]


def test_transactions_through_the_helper(host: Path) -> None:
    """Full apply/rollback using the helper process instead of direct writes (no sudo in tests)."""
    lay = load_layout()
    ex = HelperExecutor(lay, use_sudo=False)
    ex.cmd = [sys.executable, str(HELPER)]
    os.environ["AILX_HELPER_TEST_ROOT"] = str(host)
    try:
        mgr = Manager(lay, ex)
        txn = mgr.apply(profiles.plan(lay, profiles.load(lay, "inference"), include_experimental=True))
        assert txn["executor"] == "helper" and (host / "proc/sys/vm/swappiness").read_text() == "10"
        mgr.rollback()
        assert (host / "proc/sys/vm/swappiness").read_text().strip() == "60"
        with pytest.raises(ExecutorError, match="refused"):
            ex.write("/etc/hostname", "x")
    finally:
        del os.environ["AILX_HELPER_TEST_ROOT"]
