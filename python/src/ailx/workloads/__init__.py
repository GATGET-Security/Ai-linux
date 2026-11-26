# SPDX-License-Identifier: MIT
"""Workload manifests (WP3)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from ailx.core import Doc, canonical_bytes, sha256_hex, validate


def resolve(path: str) -> str:
    """Expand ``$VARS`` and ``~`` in binary/model paths at execution time (manifests stay portable)."""
    return os.path.expanduser(os.path.expandvars(path))


def load_workload(path: str | Path) -> Doc:
    doc: Doc = json.loads(Path(path).read_text())
    validate(doc, "ailx.workload/v0")
    return doc


def workload_sha256(doc: Doc) -> str:
    return sha256_hex(canonical_bytes(doc))


def apply_overrides(workload: Doc, overrides: dict[str, Any] | None) -> Doc:
    """Arm-specific parameter overrides (shallow merge into ``params``)."""
    out = copy.deepcopy(workload)
    if overrides:
        out["params"] = {**out.get("params", {}), **overrides}
    validate(out, "ailx.workload/v0")
    return out
