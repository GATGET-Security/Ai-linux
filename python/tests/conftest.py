# SPDX-License-Identifier: MIT
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakesys import build_single_llc_vm, build_two_ccd_desktop

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def desktop_root(tmp_path: Path) -> Path:
    return build_two_ccd_desktop(tmp_path / "desktop")


@pytest.fixture
def vm_root(tmp_path: Path) -> Path:
    return build_single_llc_vm(tmp_path / "vm")


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Writable fake host root; all ailx paths are redirected below tmp_path."""
    from systemfix import build_host

    root = build_host(tmp_path / "root")
    monkeypatch.setenv("AILX_ROOT", str(root))
    monkeypatch.setenv("AILX_STATE_DIR", str(root / "var/lib/ailx"))
    monkeypatch.setenv("AILX_ENGINES_DIR", str(root / "opt/ailx/engines"))
    monkeypatch.delenv("AILX_ENGINE_CATALOG", raising=False)
    return root


@pytest.fixture
def fake_gpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from systemfix import install_fake_nvidia_smi

    state = tmp_path / "nvsmi.json"
    install_fake_nvidia_smi(tmp_path / "fakebin", state)
    monkeypatch.setenv("PATH", f"{tmp_path / 'fakebin'}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("FAKE_NVSMI_STATE", str(state))
    return state
