# SPDX-License-Identifier: MIT
"""Privilege boundary: the only code paths that change system state.

* :class:`DirectExecutor` writes itself. Used when running as root, or below a test/chroot root
  (``AILX_ROOT`` != "/"), where no privilege is involved.
* :class:`HelperExecutor` sends one JSON request per write to the privileged
  ``ailx-apply-helper`` (installed by ``ailx system install``) through ``sudo -n``. The helper
  enforces its own allow-list of targets and values; it never trusts this process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Protocol

from ailx.system.paths import Layout


class ExecutorError(RuntimeError):
    pass


class Executor(Protocol):
    name: str

    def write(self, target: str, value: str) -> None: ...


class DirectExecutor:
    name = "direct"

    def __init__(self, layout: Layout) -> None:
        self.layout = layout

    def write(self, target: str, value: str) -> None:
        if target.startswith("nvidia:"):
            exe = shutil.which("nvidia-smi")
            if not exe:
                raise ExecutorError("nvidia-smi not found")
            r = subprocess.run(
                [exe, "-i", target.split(":", 1)[1], "-pl", value], capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                raise ExecutorError(f"nvidia-smi -pl failed: {r.stdout.strip()} {r.stderr.strip()}")
            return
        host = self.layout.host(target)
        real = os.path.realpath(host)
        base = os.path.realpath(self.layout.root)
        rel = "/" + os.path.relpath(real, base)
        if not real.startswith(base) or not (rel.startswith("/sys/") or rel.startswith("/proc/sys/")):
            raise ExecutorError(f"{target} resolves outside /sys or /proc/sys")
        try:
            fd = os.open(real, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.write(fd, value.encode())
            finally:
                os.close(fd)
        except OSError as exc:
            raise ExecutorError(f"write {target}: {exc}") from exc


class HelperExecutor:
    name = "helper"

    def __init__(self, layout: Layout, use_sudo: bool = True) -> None:
        self.layout = layout
        self.cmd = (["sudo", "-n"] if use_sudo else []) + [layout.helper]

    def write(self, target: str, value: str) -> None:
        req = json.dumps({"op": "write", "target": target, "value": value})
        try:
            r = subprocess.run(self.cmd, input=req, capture_output=True, text=True, timeout=60)
        except OSError as exc:
            raise ExecutorError(f"cannot run helper {self.cmd}: {exc}") from exc
        try:
            resp = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        except ValueError:
            resp = {}
        if r.returncode != 0 or not resp.get("ok"):
            detail = resp.get("error") or r.stderr.strip() or f"exit {r.returncode}"
            raise ExecutorError(f"helper refused {target}={value!r}: {detail}")


def default_executor(layout: Layout) -> Executor:
    if str(layout.root) != "/" or os.geteuid() == 0:
        return DirectExecutor(layout)
    return HelperExecutor(layout)
