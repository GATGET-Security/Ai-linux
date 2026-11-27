# SPDX-License-Identifier: MIT
"""Knob registry and mechanisms: resolve targets, read current values, validate desired values.

Reads are unprivileged (sysfs/procfs are world-readable). Writes are never done here; they go
through an :mod:`ailx.system.executor` (direct as root, or via the privileged helper).
"""

from __future__ import annotations

import glob
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from ailx.core import Doc, validate
from ailx.system.paths import Layout


@dataclass(frozen=True)
class Target:
    knob: str
    target: str  # absolute host path, or "nvidia:<index>"
    choices: tuple[str, ...] = ()  # options offered by the kernel (bracketed files)


@cache
def registry() -> dict[str, Doc]:
    raw = json.loads(resources.files("ailx.system").joinpath("data/knobs.json").read_text())
    out: dict[str, Doc] = {}
    for k in raw:
        validate(k, "ailx.knob/v0")
        out[k["id"]] = k
    return out


def get(knob_id: str) -> Doc:
    try:
        return registry()[knob_id]
    except KeyError:
        raise KeyError(f"unknown knob {knob_id!r}") from None


def _bracket_choices(text: str) -> tuple[str, ...]:
    return tuple(t.strip("[]") for t in text.split())


def _read_file(layout: Layout, path: str, mode: str) -> str | None:
    try:
        text = layout.host(path).read_text().strip()
    except OSError:
        return None
    if mode == "bracketed":
        m = re.search(r"\[([^\]]+)\]", text)
        return m.group(1) if m else text
    return text


def nvidia_smi() -> str | None:
    return shutil.which("nvidia-smi")


def nvidia_query(index: str | None = None) -> list[dict[str, str]]:
    """GPU power data via nvidia-smi (absent driver → empty list, never an exception)."""
    exe = nvidia_smi()
    if not exe:
        return []
    cmd = [
        exe,
        "--query-gpu=index,power.limit,power.default_limit,power.min_limit,power.max_limit",
        "--format=csv,noheader,nounits",
    ]
    if index is not None:
        cmd += ["-i", index]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in r.stdout.strip().splitlines() if r.returncode == 0 else []:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5:
            rows.append(dict(zip(("index", "limit", "default", "min", "max"), parts, strict=True)))
    return rows


def targets(layout: Layout, knob: Doc) -> list[Target]:
    mech = knob["mechanism"]
    kind = mech["type"]
    if kind == "file":
        p = layout.host(mech["path"])
        if not p.exists():
            return []
        choices = _bracket_choices(p.read_text()) if mech.get("read") == "bracketed" else ()
        return [Target(knob["id"], mech["path"], choices)]
    if kind == "file-glob":
        found = sorted(glob.glob(str(layout.host(mech["glob"]))))
        rel = ["/" + str(Path(f).relative_to(layout.root)) for f in found]
        return [Target(knob["id"], r) for r in rel]
    if kind == "nvidia-power-limit":
        return [Target(knob["id"], f"nvidia:{row['index']}") for row in nvidia_query()]
    return []


def read(layout: Layout, knob: Doc, target: str) -> str | None:
    mech = knob["mechanism"]
    if mech["type"] in ("file", "file-glob"):
        return _read_file(layout, target, mech.get("read", "plain"))
    if mech["type"] == "nvidia-power-limit":
        rows = nvidia_query(target.split(":", 1)[1])
        return _watts(rows[0]["limit"]) if rows else None
    return None


def _watts(text: str) -> str:
    return str(round(float(text)))


def desired_value(knob: Doc, value: str, target: str) -> str:
    """Validate ``value`` against the knob's domain and translate it into the value to write."""
    dom: dict[str, Any] = knob.get("domain") or {}
    if "enum" in dom:
        if value not in dom["enum"]:
            raise ValueError(f"{knob['id']}: {value!r} not in {dom['enum']}")
        return value
    if "int_range" in dom:
        lo, hi = dom["int_range"]
        if not re.fullmatch(r"\d+", value) or not lo <= int(value) <= hi:
            raise ValueError(f"{knob['id']}: {value!r} outside [{lo}, {hi}]")
        return value
    if "fraction_range" in dom:
        lo, hi = dom["fraction_range"]
        frac = float(value)
        if not lo <= frac <= hi:
            raise ValueError(f"{knob['id']}: fraction {frac} outside [{lo}, {hi}]")
        rows = nvidia_query(target.split(":", 1)[1])
        if not rows:
            raise ValueError(f"{knob['id']}: cannot query power limits for {target}")
        watts = round(float(rows[0]["default"]) * frac)
        if watts < float(rows[0]["min"]):
            raise ValueError(f"{knob['id']}: {watts} W below the minimum limit {rows[0]['min']} W")
        return str(watts)
    raise ValueError(f"{knob['id']}: knob has no writable domain")
