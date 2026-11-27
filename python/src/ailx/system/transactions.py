# SPDX-License-Identifier: MIT
"""Transactional configuration changes with journal, verification, dead-man confirmation and rollback.

Invariants:

1. The journal (with every target's *before* value) is fsynced **before** the first write.
2. Every write is verified by reading the value back.
3. Any failure during apply rolls back the already-applied changes in reverse order.
4. Only one profile transaction is active at a time; switching profiles first restores the
   previous values (so "before" always means the state AI Linux found).
5. Rollback never overwrites a value that someone else changed after AI Linux applied it
   (drift) unless forced.
6. Optional dead-man timer (ADR-0008 T1): unless ``ailx confirm`` is run before the deadline, a
   detached watchdog rolls the transaction back.
7. Interrupted transactions (crash mid-apply) are detected and can be recovered.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ailx.core import Doc
from ailx.system import knobs
from ailx.system.executor import Executor, ExecutorError, default_executor
from ailx.system.paths import Layout
from ailx.system.profiles import Plan

INTERRUPTED = ("prepared", "applying")
TXN_ID = re.compile(r"^txn-[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
ACTIVE = ("pending-confirmation", "committed")


class TransactionError(RuntimeError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _atomic_write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class Manager:
    def __init__(self, layout: Layout, executor: Executor | None = None) -> None:
        self.layout = layout
        self.executor = executor or default_executor(layout)
        self.dir = layout.state_dir
        self.txn_dir = self.dir / "transactions"

    # -- persistence -------------------------------------------------------------------------
    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / "lock", os.O_CREAT | os.O_RDWR, 0o660)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TransactionError("another ailx operation holds the state lock") from None
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def state(self) -> Doc:
        try:
            doc: Doc = json.loads((self.dir / "state.json").read_text())
        except (OSError, ValueError):
            return {"schema": "ailx.system-state/v0", "active_profile": None, "active_txn": None, "history": []}
        if doc.get("active_txn") is not None and not TXN_ID.match(str(doc["active_txn"])):
            raise TransactionError(f"state.json names an invalid transaction id {doc['active_txn']!r}")
        return doc

    def reconcile(self) -> str | None:
        """Runtime-only changes do not survive a reboot: an active transaction from an earlier boot is
        marked ``expired-by-reboot`` (values are back at their boot defaults) and deactivated."""
        st = self.state()
        tid = st.get("active_txn")
        if not tid:
            return None
        txn = self.load(tid)
        bid = self.boot_id()
        if txn.get("boot_id") and bid and txn["boot_id"] != bid and txn["status"] in ACTIVE:
            txn["status"] = "expired-by-reboot"
            self._event(txn, f"boot id changed ({txn['boot_id']} -> {bid}); runtime changes were reset by the reboot")
            self._save(txn)
            st.update(active_profile=None, active_txn=None)
            self._save_state(st)
            return str(tid)
        return None

    def _save_state(self, st: Doc) -> None:
        _atomic_write(self.dir / "state.json", st)

    def boot_id(self) -> str | None:
        try:
            return self.layout.host("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            return None

    def load(self, txn_id: str) -> Doc:
        if not TXN_ID.match(txn_id or ""):
            raise TransactionError(f"invalid transaction id {txn_id!r}")
        try:
            doc: Doc = json.loads((self.txn_dir / f"{txn_id}.json").read_text())
            return doc
        except OSError:
            raise TransactionError(f"unknown transaction {txn_id}") from None

    def _save(self, txn: Doc) -> None:
        _atomic_write(self.txn_dir / f"{txn['id']}.json", txn)

    def _event(self, txn: Doc, msg: str) -> None:
        txn.setdefault("events", []).append({"t": _now(), "msg": msg})

    def all(self) -> list[Doc]:
        if not self.txn_dir.is_dir():
            return []
        docs = [json.loads(p.read_text()) for p in self.txn_dir.glob("txn-*.json")]
        return sorted(docs, key=lambda d: d["created_at"])

    # -- operations --------------------------------------------------------------------------
    def apply(
        self, plan: Plan, confirm_timeout: float | None = None, replace: bool = False, spawn_watchdog: bool = True
    ) -> Doc:
        cmd = getattr(self.executor, "cmd", None)
        if confirm_timeout and spawn_watchdog and cmd and cmd[0] == "sudo":
            probe = subprocess.run([*cmd[:2], "-l", self.layout.helper], capture_output=True)
            if probe.returncode != 0:
                raise TransactionError(
                    "--confirm-within needs passwordless sudo for the helper (the watchdog runs unattended); "
                    "enable it with 'ailx system sudoers enable' or run as root"
                )
        with self.lock():
            self.reconcile()
            st = self.state()
            if self.interrupted():
                raise TransactionError("interrupted transaction found; run 'ailx rollback --recover' first")
            if st.get("active_txn"):
                if not replace:
                    raise TransactionError(
                        f"profile {st['active_profile']!r} is active ({st['active_txn']}); "
                        "roll back first or use --replace"
                    )
                self._rollback_locked(st["active_txn"], force=False, reason=f"replaced by profile {plan.profile}")
                st = self.state()
            txn: Doc = {
                "schema": "ailx.transaction/v0",
                "id": f"txn-{_dt.datetime.now(_dt.UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(3)}",
                "created_at": _now(),
                "boot_id": self.boot_id(),
                "profile": plan.profile,
                "include_experimental": plan.include_experimental,
                "executor": self.executor.name,
                "status": "prepared",
                "confirm_deadline": None,
                "confirmed": False,
                "changes": self._fresh_changes(plan),
                "skipped": [
                    {"knob": s.knob, "target": s.target, "reason": s.reason} for s in plan.steps if s.action == "skip"
                ],
                "events": [],
            }
            self._save(txn)  # invariant 1: journal before any write
            txn["status"] = "applying"
            self._save(txn)
            for ch in txn["changes"]:
                try:
                    self._checked_write(ch["knob"], ch["target"], ch["after"])
                    ch["applied"] = True
                    now = knobs.read(self.layout, knobs.get(ch["knob"]), ch["target"])
                    if now != ch["after"]:
                        raise ExecutorError(f"verification failed: read back {now!r}, expected {ch['after']!r}")
                    ch["verified"] = True
                    self._save(txn)
                except ExecutorError as exc:
                    self._event(txn, f"apply failed at {ch['knob']} {ch['target']}: {exc}")
                    self._revert(txn, force=True)
                    txn["status"] = "failed-rolled-back" if txn["status"] != "rollback-failed" else "rollback-failed"
                    self._save(txn)
                    raise TransactionError(f"apply failed and was rolled back: {exc}") from exc
            if confirm_timeout:
                txn["status"] = "pending-confirmation"
                txn["confirm_deadline"] = time.time() + confirm_timeout
            else:
                txn["status"] = "committed"
            self._save(txn)
            if txn["changes"]:
                st.update(active_profile=plan.profile, active_txn=txn["id"])
            st["history"] = [*st.get("history", []), txn["id"]]
            self._save_state(st)
        if confirm_timeout and spawn_watchdog and txn["changes"]:
            self._spawn_watchdog(txn["id"])
        return txn

    def _fresh_changes(self, plan: Plan) -> list[Doc]:
        """Re-read current values under the lock (plan may be stale, e.g. after --replace rolled back
        the previous profile); steps planned as no-change are re-evaluated too."""
        out = []
        for s in plan.steps:
            if s.action not in ("change", "no-change") or s.target is None or s.desired is None:
                continue
            now = knobs.read(self.layout, knobs.get(s.knob), s.target)
            if now is None:
                raise TransactionError(f"{s.knob} {s.target}: current value unreadable at apply time")
            if now != s.desired:
                out.append(
                    {
                        "knob": s.knob,
                        "target": s.target,
                        "before": now,
                        "after": s.desired,
                        "applied": False,
                        "verified": False,
                    }
                )
        return out

    def _checked_write(self, knob_id: str, target: str, value: str) -> None:
        try:
            knobs.validate_write(self.layout, knob_id, target, value)
        except (ValueError, KeyError) as exc:
            raise ExecutorError(f"refused by registry validation: {exc}") from exc
        self.executor.write(target, value)

    def _spawn_watchdog(self, txn_id: str) -> None:
        env = dict(os.environ, AILX_STATE_DIR=str(self.dir), AILX_ROOT=str(self.layout.root))
        subprocess.Popen(
            [sys.executable, "-m", "ailx.system.watchdog", txn_id],
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def confirm(self, txn_id: str | None = None) -> Doc:
        with self.lock():
            txn = self.load(txn_id or self.state().get("active_txn") or "")
            if txn["status"] != "pending-confirmation":
                raise TransactionError(f"{txn['id']} is {txn['status']}, nothing to confirm")
            if time.time() > float(txn["confirm_deadline"]):
                raise TransactionError(f"{txn['id']}: confirmation deadline passed (watchdog will roll back)")
            txn.update(status="committed", confirmed=True)
            self._event(txn, "confirmed by operator")
            self._save(txn)
            return txn

    def drift(self, txn: Doc | None = None) -> list[Doc]:
        txn = txn or (self.load(self.state()["active_txn"]) if self.state().get("active_txn") else None)
        if not txn:
            return []
        out = []
        for ch in txn["changes"]:
            if ch["applied"]:
                now = knobs.read(self.layout, knobs.get(ch["knob"]), ch["target"])
                if now != ch["after"]:
                    out.append({"knob": ch["knob"], "target": ch["target"], "expected": ch["after"], "current": now})
        return out

    def _revert(self, txn: Doc, force: bool) -> list[Doc]:
        """Restore *before* values of applied changes in reverse order. Returns skipped (drifted) changes."""
        skipped, failed = [], []
        for ch in reversed(txn["changes"]):
            if not ch["applied"] or ch.get("reverted"):
                continue
            k = knobs.get(ch["knob"])
            now = knobs.read(self.layout, k, ch["target"])
            if now != ch["after"] and not force:
                skipped.append({"knob": ch["knob"], "target": ch["target"], "current": now, "expected": ch["after"]})
                self._event(txn, f"drift on {ch['target']}: {now!r} != {ch['after']!r}; not restored")
                continue
            try:
                self._checked_write(ch["knob"], ch["target"], ch["before"])
                if knobs.read(self.layout, k, ch["target"]) != ch["before"]:
                    raise ExecutorError("read-back after restore differs")
                ch["reverted"] = True
            except ExecutorError as exc:
                failed.append(ch["target"])
                self._event(txn, f"restore failed for {ch['target']}: {exc}")
        if failed:
            txn["status"] = "rollback-failed"
        return skipped

    def _rollback_locked(self, txn_id: str, force: bool, reason: str) -> Doc:
        st = self.state()
        txn = self.load(txn_id)
        if txn["status"] not in ACTIVE:
            raise TransactionError(f"{txn_id} is {txn['status']}; only active transactions can be rolled back")
        if st.get("active_txn") != txn_id and not force:
            raise TransactionError(f"{txn_id} is not the active transaction ({st.get('active_txn')})")
        skipped = self._revert(txn, force)
        if txn["status"] != "rollback-failed":
            txn["status"] = "rolled-back-partial" if skipped else "rolled-back"
        txn["rolled_back_at"] = _now()
        self._event(txn, f"rollback: {reason}")
        self._save(txn)
        if st.get("active_txn") == txn_id and txn["status"] != "rollback-failed":
            st.update(active_profile=None, active_txn=None)
            self._save_state(st)
        return txn

    def rollback(self, txn_id: str | None = None, force: bool = False, reason: str = "operator request") -> Doc:
        with self.lock():
            self.reconcile()
            tid = txn_id or self.state().get("active_txn")
            if not tid:
                raise TransactionError("no active transaction")
            return self._rollback_locked(tid, force, reason)

    def interrupted(self) -> list[Doc]:
        return [t for t in self.all() if t["status"] in INTERRUPTED]

    def recover(self) -> list[Doc]:
        """Restore *before* values of transactions interrupted mid-apply (crash, power loss)."""
        out = []
        with self.lock():
            for txn in self.interrupted():
                for ch in txn["changes"]:
                    ch["applied"] = True  # unknown → restore idempotently
                self._revert(txn, force=True)
                if txn["status"] != "rollback-failed":
                    txn["status"] = "recovered"
                self._event(txn, "recovered after interruption")
                self._save(txn)
                out.append(txn)
            st = self.state()
            if st.get("active_txn") in {t["id"] for t in out}:
                st.update(active_profile=None, active_txn=None)
                self._save_state(st)
        return out

    def watchdog_expire(self, txn_id: str) -> str:
        """Called by the watchdog at the deadline: roll back unless confirmed."""
        with self.lock():
            txn = self.load(txn_id)
            if txn["status"] != "pending-confirmation":
                return str(txn["status"])
            done = self._rollback_locked(txn_id, force=False, reason="confirmation deadline expired")
            return str(done["status"])
