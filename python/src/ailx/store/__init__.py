# SPDX-License-Identifier: MIT
"""Content-addressed, immutable result store with a SQLite index (WP7)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import zstandard

from ailx.core import Doc, canonical_bytes, compute_id, sha256_hex, validate

TYPE_DIRS = {"hwp": "hardware-profiles", "env": "fingerprints", "cmp": "campaigns", "res": "results", "ana": "analyses"}


class IntegrityError(RuntimeError):
    pass


class Store:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "index.sqlite")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, schema TEXT, path TEXT, campaign_id TEXT,"
            " arm_id TEXT, order_index INTEGER, status TEXT, created TEXT)"
        )
        self.db.commit()

    # -- documents ---------------------------------------------------------------------------
    def _path(self, doc_id: str) -> Path:
        return self.root / "objects" / TYPE_DIRS[doc_id.split("-", 1)[0]] / f"{doc_id}.json"

    def put(self, doc: Doc) -> str:
        validate(doc)
        doc_id: str = doc["id"]
        if doc_id.startswith("hwp-"):
            from ailx.probe.hardware import seal_profile

            expected = seal_profile({k: v for k, v in doc.items() if k != "id"})["id"]
        else:
            expected = compute_id(doc)
        if expected != doc_id:
            raise IntegrityError(f"id does not match content: {doc_id}")
        path = self._path(doc_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(canonical_bytes(doc))
            tmp.replace(path)
        self.db.execute(
            "INSERT OR IGNORE INTO docs VALUES (?,?,?,?,?,?,?,?)",
            (
                doc_id,
                doc["schema"],
                str(path.relative_to(self.root)),
                doc.get("campaign_id"),
                doc.get("arm_id"),
                doc.get("order_index"),
                doc.get("status"),
                doc.get("started_at") or doc.get("created_at"),
            ),
        )
        self.db.commit()
        return doc_id

    def get(self, doc_id: str) -> Doc:
        path = self._path(doc_id)
        doc: Doc = json.loads(path.read_bytes())
        if doc.get("id") != doc_id:
            raise IntegrityError(f"{path}: stored id mismatch")
        return doc

    def has(self, doc_id: str) -> bool:
        return self._path(doc_id).exists()

    def results_for(self, campaign_id: str) -> list[Doc]:
        rows = self.db.execute(
            "SELECT id FROM docs WHERE campaign_id=? AND schema='ailx.result/v0' ORDER BY order_index", (campaign_id,)
        ).fetchall()
        return [self.get(r[0]) for r in rows]

    def list_docs(self, schema: str | None = None) -> list[tuple[str, str]]:
        q = "SELECT id, schema FROM docs" + (" WHERE schema=?" if schema else "") + " ORDER BY created"
        return [(r[0], r[1]) for r in self.db.execute(q, (schema,) if schema else ())]

    def latest_analysis(self, campaign_id: str) -> Doc | None:
        row = self.db.execute(
            "SELECT id FROM docs WHERE campaign_id=? AND schema='ailx.analysis/v0' ORDER BY created DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return self.get(row[0]) if row else None

    # -- blobs ---------------------------------------------------------------------------------
    def put_blob(self, data: bytes) -> str:
        digest = sha256_hex(data)
        path = self.root / "blobs" / digest[:2] / f"{digest}.zst"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(zstandard.ZstdCompressor(level=10).compress(data))
            tmp.replace(path)
        return f"sha256:{digest}"

    def put_json_blob(self, obj: Any) -> str:
        return self.put_blob(json.dumps(obj, separators=(",", ":")).encode())

    def get_blob(self, ref: str) -> bytes:
        digest = ref.split(":", 1)[1]
        data: bytes = zstandard.ZstdDecompressor().decompress(
            (self.root / "blobs" / digest[:2] / f"{digest}.zst").read_bytes()
        )
        if sha256_hex(data) != digest:
            raise IntegrityError(f"blob {ref} corrupted")
        return data

    # -- verification -----------------------------------------------------------------------
    def verify(self) -> list[str]:
        problems: list[str] = []
        for path in sorted((self.root / "objects").rglob("*.json")):
            try:
                doc = json.loads(path.read_bytes())
                validate(doc)
                if doc["id"].startswith("hwp-"):
                    from ailx.probe.hardware import seal_profile

                    expected = seal_profile({k: v for k, v in doc.items() if k != "id"})["id"]
                else:
                    expected = compute_id(doc)
                if expected != doc["id"] or path.stem != doc["id"]:
                    problems.append(f"{path.relative_to(self.root)}: content hash mismatch")
            except Exception as exc:
                problems.append(f"{path.relative_to(self.root)}: {exc}")
        for path in sorted((self.root / "blobs").rglob("*.zst")):
            try:
                self.get_blob("sha256:" + path.stem)
            except Exception as exc:
                problems.append(f"{path.relative_to(self.root)}: {exc}")
        return problems
