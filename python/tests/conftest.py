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
