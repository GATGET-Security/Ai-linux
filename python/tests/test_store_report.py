# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ailx.core import seal
from ailx.report import validate_claims
from ailx.store import IntegrityError, Store


def _result(**kw: object) -> dict:  # type: ignore[type-arg]
    doc = {
        "schema": "ailx.result/v0",
        "campaign_id": None,
        "arm_id": None,
        "block": None,
        "order_index": 0,
        "workload_name": "w",
        "workload_sha256": "0" * 64,
        "fingerprint_id": "env-" + "1" * 64,
        "hardware_profile_id": "hwp-" + "2" * 64,
        "measurement_kind": "simulated",
        "started_at": "2026-11-26T00:00:00.000000Z",
        "duration_s": 1.0,
        "status": "ok",
        "metrics": {"x": {"value": 1.0, "unit": "u"}},
        "tool": {"name": "t"},
        "telemetry": {},
        "energy": {},
        "stability_events": [],
    }
    doc.update(kw)
    return seal(doc)


def test_put_get_and_tamper_detection(tmp_path: Path) -> None:
    st = Store(tmp_path)
    r = _result()
    st.put(r)
    assert st.get(r["id"]) == r and st.verify() == []
    path = next((tmp_path / "objects").rglob("*.json"))
    doc = json.loads(path.read_text())
    doc["metrics"]["x"]["value"] = 2.0
    path.write_text(json.dumps(doc))
    assert any("hash mismatch" in p for p in st.verify())
    with pytest.raises(IntegrityError):
        st.put(dict(r, duration_s=9.0))  # id no longer matches content


def test_blobs_roundtrip_and_corruption(tmp_path: Path) -> None:
    st = Store(tmp_path)
    ref = st.put_blob(b"hello" * 1000)
    assert st.get_blob(ref) == b"hello" * 1000
    blob = next((tmp_path / "blobs").rglob("*.zst"))
    import zstandard

    blob.write_bytes(zstandard.ZstdCompressor().compress(b"evil"))
    with pytest.raises(IntegrityError):
        st.get_blob(ref)


def test_validate_claims() -> None:
    text = "Decode improved by 7% on the 4090.\nDecode improved by 7% [ana-0123456789ab].\nNo numbers here.\n"
    assert [n for n, _ in validate_claims(text)] == [1]
