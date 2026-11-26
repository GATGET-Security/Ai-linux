# SPDX-License-Identifier: MIT
"""Conformance corpus: every valid fixture validates, every invalid fixture is rejected."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ailx.core import SCHEMA_FILES, ValidationError, canonical_bytes, compute_id, validate

FIX = Path(__file__).resolve().parents[2] / "schemas" / "fixtures"
BY_FILE = {v.removesuffix(".schema.json"): k for k, v in SCHEMA_FILES.items()}


def cases(kind: str) -> list[tuple[str, Path]]:
    return [(BY_FILE[p.parent.name], p) for p in sorted((FIX / kind).glob("*/*.json"))]


@pytest.mark.parametrize(("schema", "path"), cases("valid"), ids=lambda x: getattr(x, "stem", x))
def test_valid_fixtures(schema: str, path: Path) -> None:
    validate(json.loads(path.read_text()), schema)


@pytest.mark.parametrize(("schema", "path"), cases("invalid"), ids=lambda x: getattr(x, "stem", x))
def test_invalid_fixtures(schema: str, path: Path) -> None:
    with pytest.raises(ValidationError):
        validate(json.loads(path.read_text()), schema)


def test_every_schema_has_both_kinds_of_fixtures() -> None:
    for name in BY_FILE:
        assert list((FIX / "valid" / name).glob("*.json")), name
        assert list((FIX / "invalid" / name).glob("*.json")), name


def test_canonical_json_and_ids_are_order_independent() -> None:
    a = {"schema": "ailx.result/v0", "b": 1.0, "a": [1, 2]}
    b = {"a": [1, 2], "b": 1, "schema": "ailx.result/v0"}
    assert canonical_bytes(a) == canonical_bytes(b)
    assert compute_id(a) == compute_id(b)
    assert compute_id(a).startswith("res-")
