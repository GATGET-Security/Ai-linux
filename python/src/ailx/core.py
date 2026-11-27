# SPDX-License-Identifier: MIT
"""Schemas, canonical JSON and content-addressed IDs.

Canonical JSON follows RFC 8785 (JCS). A document's ID is
``<prefix>-sha256(canonical(document without "id"))``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import rfc8785

Doc = dict[str, Any]

PREFIXES = {
    "ailx.hardware-profile/v0": "hwp",
    "ailx.environment-fingerprint/v0": "env",
    "ailx.campaign/v0": "cmp",
    "ailx.result/v0": "res",
    "ailx.analysis/v0": "ana",
    "ailx.capabilities/v0": "cap",
    "ailx.model-analysis/v0": "mdl",
    "ailx.tuning-record/v0": "tun",
}
SCHEMA_FILES = {
    "ailx.hardware-profile/v0": "hardware-profile.v0.schema.json",
    "ailx.environment-fingerprint/v0": "environment-fingerprint.v0.schema.json",
    "ailx.workload/v0": "workload.v0.schema.json",
    "ailx.campaign/v0": "campaign.v0.schema.json",
    "ailx.result/v0": "result.v0.schema.json",
    "ailx.analysis/v0": "analysis.v0.schema.json",
    "ailx.knob/v0": "knob.v0.schema.json",
    "ailx.sample-stream/v0": "sample-stream.v0.schema.json",
    "ailx.profile/v0": "profile.v0.schema.json",
    "ailx.engine-manifest/v0": "engine-manifest.v0.schema.json",
    "ailx.capabilities/v0": "capabilities.v0.schema.json",
    "ailx.model-analysis/v0": "model-analysis.v0.schema.json",
    "ailx.tuning-record/v0": "tuning-record.v0.schema.json",
    "ailx.runtime-instance/v0": "runtime-instance.v0.schema.json",
}


class ValidationError(ValueError):
    """A document does not conform to its schema."""


def schema_dir() -> Path:
    """Locate the canonical schemas (repository checkout or installed wheel)."""
    here = Path(__file__).resolve()
    installed = here.parent / "_schemas"
    if installed.is_dir():
        return installed
    for parent in here.parents:
        candidate = parent / "schemas"
        if (candidate / "result.v0.schema.json").is_file():
            return candidate
    raise FileNotFoundError("canonical schemas not found")


@cache
def load_schema(schema_id: str) -> Doc:
    path = schema_dir() / SCHEMA_FILES[schema_id]
    data: Doc = json.loads(path.read_text())
    return data


def validate(doc: Doc, schema_id: str | None = None) -> None:
    sid = schema_id or doc.get("schema")
    if not isinstance(sid, str) or sid not in SCHEMA_FILES:
        raise ValidationError(f"unknown or missing schema id: {sid!r}")
    validator = jsonschema.Draft202012Validator(
        load_schema(sid), format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        msg = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:5])
        raise ValidationError(f"{sid}: {msg}")


def canonical_bytes(doc: Any) -> bytes:
    out: bytes = rfc8785.dumps(doc)
    return out


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_id(doc: Doc) -> str:
    """Return the content-addressed ID of ``doc`` (the ``id`` field is excluded)."""
    prefix = PREFIXES[doc["schema"]]
    body = {k: v for k, v in doc.items() if k != "id"}
    return f"{prefix}-{sha256_hex(canonical_bytes(body))}"


def seal(doc: Doc) -> Doc:
    """Assign the content-addressed ID and validate."""
    doc = dict(doc)
    doc["id"] = compute_id(doc)
    validate(doc)
    return doc


def utcnow() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def file_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
