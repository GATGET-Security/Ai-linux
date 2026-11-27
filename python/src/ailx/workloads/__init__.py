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
    """Expand ``$VARS``/``~`` and ``engine:<engine>/<binary>`` references at execution time."""
    if path.startswith("engine:"):
        from ailx.system.engines import resolve_engine_ref
        from ailx.system.paths import load_layout

        return resolve_engine_ref(path, load_layout())[0]
    return os.path.expanduser(os.path.expandvars(path))


def bundle(binary: str) -> Doc:
    """Runtime-bundle record for fingerprints; engine references carry their manifest digest."""
    if binary.startswith("engine:"):
        from ailx.system.engines import manifest_digest, resolve_engine_ref
        from ailx.system.paths import load_layout

        path, m = resolve_engine_ref(binary, load_layout())
        return {
            "name": binary,
            "version": f"{m['version']} ({m['variant']})",
            "digest": manifest_digest(m),
            "path": path,
        }
    return {"name": binary, "digest": None, "path": resolve(binary)}


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
