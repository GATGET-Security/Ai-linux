# SPDX-License-Identifier: MIT
"""Filesystem layout and configuration of the system layer.

Resolution order for every location: environment variable → /etc/ailx/ailx.toml (written by
``ailx system install``) → system default if it exists and is writable → per-user default.
``AILX_ROOT`` relocates *all* host paths (sysfs/procfs reads, knob writes, installation) under a
directory; it is used for isolated tests and chroot-style installs and is "/" in production.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

SYSTEM_CONFIG = "/etc/ailx/ailx.toml"
DEFAULT_HELPER = "/usr/local/libexec/ailx-apply-helper"


@dataclass(frozen=True)
class Layout:
    root: Path  # host filesystem root ("/" in production)
    state_dir: Path  # transactions, state.json, lock
    engines_dir: Path  # isolated engine installations
    helper: str  # privileged apply helper (system mode)
    mode: str  # "system" | "user"

    def host(self, path: str) -> Path:
        """Map an absolute host path (e.g. /sys/...) below the configured root."""
        return self.root / path.lstrip("/")


def _writable_dir(p: Path) -> bool:
    return p.is_dir() and os.access(p, os.W_OK | os.X_OK)


def load_layout() -> Layout:
    root = Path(os.environ.get("AILX_ROOT", "/"))
    cfg: dict[str, str] = {}
    cfg_path = root / SYSTEM_CONFIG.lstrip("/")
    if cfg_path.is_file():
        with open(cfg_path, "rb") as f:
            cfg = {k: str(v) for k, v in tomllib.load(f).get("paths", {}).items()}
    home = Path(os.environ.get("HOME", "/root"))
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))

    def pick(env: str, key: str, system: Path, user: Path) -> tuple[Path, bool]:
        if env in os.environ:
            return Path(os.environ[env]), False
        if key in cfg:
            return Path(cfg[key]), True
        if _writable_dir(system):
            return system, True
        return user, False

    state, s_sys = pick("AILX_STATE_DIR", "state_dir", root / "var/lib/ailx", xdg_state / "ailx")
    engines, _ = pick("AILX_ENGINES_DIR", "engines_dir", root / "opt/ailx/engines", xdg_data / "ailx/engines")
    helper = os.environ.get("AILX_APPLY_HELPER", cfg.get("helper", DEFAULT_HELPER))
    return Layout(root=root, state_dir=state, engines_dir=engines, helper=helper, mode="system" if s_sys else "user")


def system_layout() -> Layout:
    """The system-mode layout used by ``ailx system install/uninstall`` (independent of the caller's
    current resolution, which may fall back to per-user paths before installation)."""
    root = Path(os.environ.get("AILX_ROOT", "/"))
    return Layout(
        root=root,
        state_dir=root / "var/lib/ailx",
        engines_dir=root / "opt/ailx/engines",
        helper=DEFAULT_HELPER,
        mode="system",
    )
