# SPDX-License-Identifier: MIT
"""AI Linux measurement foundation (Phase 1.1)."""

from pathlib import Path as _Path

__version__ = "0.1.0"
try:  # set by the .deb build (packaging/deb/build_deb.py)
    PACKAGE_VERSION: str | None = (_Path(__file__).parent / "_package_version").read_text().strip()
except OSError:
    PACKAGE_VERSION = None
