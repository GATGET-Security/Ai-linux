# SPDX-License-Identifier: MIT
"""Small helpers to read procfs/sysfs below a configurable root (for fixture tests)."""

from __future__ import annotations

import os
import re
from pathlib import Path


class Root:
    """Filesystem view rooted at ``base`` ("/" on a live system)."""

    def __init__(self, base: str | Path = "/") -> None:
        self.base = Path(base)

    def path(self, p: str) -> Path:
        return self.base / p.lstrip("/")

    def rel(self, p: Path) -> str:
        """Absolute-style path of ``p`` relative to the root (for re-reading through ``read``)."""
        return "/" + str(Path(p).relative_to(self.base))

    def read(self, p: str) -> str | None:
        try:
            return self.path(p).read_text(errors="replace").strip()
        except (OSError, ValueError):
            return None

    def read_int(self, p: str) -> int | None:
        v = self.read(p)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    def glob(self, pattern: str) -> list[Path]:
        return sorted(self.base.glob(pattern.lstrip("/")))

    def exists(self, p: str) -> bool:
        return self.path(p).exists()

    def readlink_name(self, p: str) -> str | None:
        try:
            return os.path.basename(os.readlink(self.path(p)))
        except OSError:
            return None

    def is_live(self) -> bool:
        return self.base == Path("/")


def parse_cpu_list(text: str) -> list[int]:
    """Parse '0-3,8,10-11' into a sorted list of CPU numbers."""
    cpus: set[int] = set()
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            cpus.update(range(int(a), int(b) + 1))
        else:
            cpus.add(int(part))
    return sorted(cpus)


def parse_size(text: str) -> int | None:
    """Parse cache sizes like '32768K' or '96M' into bytes."""
    m = re.fullmatch(r"\s*(\d+)\s*([KMG]?)B?\s*", text or "")
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)]
    return int(m.group(1)) * mult


def bracketed(text: str | None) -> str | None:
    """Return the [selected] value of sysfs choice files like 'always [madvise] never'."""
    if text is None:
        return None
    m = re.search(r"\[([^\]]+)\]", text)
    return m.group(1) if m else text.strip()
