# SPDX-License-Identifier: MIT
"""Install / uninstall of the system layer into an isolated root."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ailx.system.install import InstallError, install, plan, uninstall
from ailx.system.paths import load_layout, system_layout


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_install_uninstall_roundtrip(host: Path) -> None:
    lay = system_layout()
    m = install(lay, with_sudoers=True, log=lambda s: None)
    helper = host / "usr/local/libexec/ailx-apply-helper"
    assert helper.read_text().startswith("#!/usr/bin/python3 -I\n") and mode(helper) == 0o755
    assert mode(host / "etc/sudoers.d/ailx") == 0o440
    assert (
        "%ailx ALL=(root) NOPASSWD: /usr/local/libexec/ailx-apply-helper" in (host / "etc/sudoers.d/ailx").read_text()
    )
    assert mode(host / "var/lib/ailx") == 0o2770
    assert (host / "etc/ailx/ailx.toml").read_text().count('state_dir = "/var/lib/ailx"') == 1
    assert (host / "etc/systemd/system/ailx-rapl-collector.service").is_file()
    assert any(i["kind"] == "group" and i.get("skipped") for i in m["items"])  # no groupadd under a test root
    # resolved layout now follows /etc/ailx/ailx.toml
    resolved = load_layout()
    assert resolved.helper == "/usr/local/libexec/ailx-apply-helper"
    install(lay, with_sudoers=True, log=lambda s: None)  # idempotent
    res = uninstall(lay, log=lambda s: None)
    assert not helper.exists() and not (host / "etc/sudoers.d/ailx").exists()
    assert not res["kept"] or all("not empty" in k for k in res["kept"])


def test_existing_foreign_file_is_not_overwritten(host: Path) -> None:
    f = host / "etc/ailx/ailx.toml"
    f.parent.mkdir(parents=True)
    f.write_text("# admin's own config\n")
    with pytest.raises(InstallError, match="exists with different content"):
        install(system_layout(), log=lambda s: None)
    install(system_layout(), force=True, log=lambda s: None)
    assert (host / "etc/ailx/ailx.toml.ailx-bak").read_text() == "# admin's own config\n"
    uninstall(system_layout(), log=lambda s: None)
    assert f.read_text() == "# admin's own config\n"  # backup restored


def test_modified_file_is_kept_on_uninstall(host: Path) -> None:
    install(system_layout(), log=lambda s: None)
    unit = host / "etc/systemd/system/ailx-rapl-collector.service"
    unit.write_text(unit.read_text() + "# local edit\n")
    res = uninstall(system_layout(), log=lambda s: None)
    assert unit.exists() and any("modified" in k for k in res["kept"])


def test_uninstall_refuses_with_active_profile_and_purge(host: Path) -> None:
    from ailx.system import profiles
    from ailx.system.transactions import Manager

    lay = system_layout()
    install(lay, log=lambda s: None)
    rl = load_layout()
    Manager(rl).apply(profiles.plan(rl, profiles.load(rl, "inference"), include_experimental=True))
    with pytest.raises(InstallError, match="rollback"):
        uninstall(lay, log=lambda s: None)
    Manager(rl).rollback()
    uninstall(lay, purge=True, log=lambda s: None)
    assert not (host / "var/lib/ailx").exists() and not (host / "opt/ailx/engines").exists()


def test_dry_run_changes_nothing(host: Path) -> None:
    before = sorted(str(p) for p in host.rglob("*"))
    res = install(system_layout(), dry_run=True, log=lambda s: None)
    assert res["dry_run"] and sorted(str(p) for p in host.rglob("*")) == before
    assert any("ailx-apply-helper" in a for a in res["actions"])


def test_plan_without_sudoers_by_default(host: Path) -> None:
    assert not any(a.path == "/etc/sudoers.d/ailx" for a in plan(system_layout()))
    assert os.geteuid() >= 0
