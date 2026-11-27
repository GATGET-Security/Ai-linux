# SPDX-License-Identifier: MIT
"""Glue between the system layer and the measurement foundation.

* :func:`platform_info` — system-layer state recorded in every environment fingerprint
  (``platform`` section): active profile, transaction, applied values, installed engines.
* :func:`profile_applied` — apply a profile transactionally around one measurement run and roll it
  back afterwards, so campaign arms can compare configurations reproducibly.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ailx import __version__
from ailx.core import Doc
from ailx.system import profiles
from ailx.system.engines import EngineManager, manifest_digest
from ailx.system.paths import Layout
from ailx.system.transactions import Manager


def platform_info(layout: Layout) -> Doc:
    mgr = Manager(layout)
    st = mgr.state()
    applied: dict[str, str] = {}
    txn_status = None
    if st.get("active_txn"):
        txn = mgr.load(st["active_txn"])
        txn_status = txn["status"]
        applied = {f"{c['knob']}@{c['target']}": c["after"] for c in txn["changes"] if c.get("applied")}
    engines = []
    em = EngineManager(layout)
    for m in em.installs():
        if not m.get("broken"):
            cur = em.current(m["engine"])
            engines.append(
                {
                    "engine": m["engine"],
                    "install_id": m["install_id"],
                    "version": m["version"],
                    "variant": m["variant"],
                    "manifest_sha256": manifest_digest(m),
                    "current": bool(cur and cur["install_id"] == m["install_id"]),
                }
            )
    return {
        "ailx_version": __version__,
        "active_profile": st.get("active_profile"),
        "active_txn": st.get("active_txn"),
        "txn_status": txn_status,
        "applied": applied,
        "engines": engines,
    }


@contextlib.contextmanager
def profile_applied(layout: Layout, name: str, include_experimental: bool) -> Iterator[Doc]:
    mgr = Manager(layout)
    prof = profiles.load(layout, name)
    p = profiles.plan(layout, prof, include_experimental=include_experimental)
    txn = mgr.apply(p)
    try:
        yield txn
    finally:
        if txn["changes"]:
            mgr.rollback(txn["id"], reason="end of measurement run")
