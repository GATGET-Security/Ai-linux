#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ailx-rapl-collector: minimal privileged helper exposing aggregated RAPL energy counters.

Why: since PLATYPUS (CVE-2020-8694/8695) powercap ``energy_uj`` is root-only, and making it
world-readable re-opens a power side channel. This helper runs as root, reads the counters,
and serves them on a Unix socket that only root and members of one group may use.
It never changes any file permission and has no write path to the system.

Mitigations against side-channel use:
* per-connection rate limit (default: at most one snapshot per 100 ms),
* package/zone-level counters only (no per-core domains are exposed),
* optional quantisation of values (``--quantum-uj``).

Protocol: the client sends ``read\\n`` and receives one JSON line:
``{"t_ns": <CLOCK_MONOTONIC>, "domains": {"package-0": <uJ>, ...}, "max_energy_range_uj": {...}}``.

Standard library only, so it can be reviewed in isolation and run without the harness.
"""

from __future__ import annotations

import argparse
import contextlib
import grp
import json
import os
import pwd
import socket
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path

VERSION = "0.1.0"


def discover(powercap: Path) -> dict[str, Path]:
    """Map zone names (e.g. 'package-0') to their directories; sub-zones (core/uncore/dram) are skipped."""
    zones: dict[str, Path] = {}
    for z in sorted(powercap.glob("*rapl:*")):
        if z.name.count(":") != 1:  # 'intel-rapl:0:0' is a sub-zone
            continue
        try:
            name = (z / "name").read_text().strip()
        except OSError:
            continue
        if (z / "energy_uj").exists():
            zones[name] = z
    return zones


def snapshot(zones: dict[str, Path], quantum: int) -> dict[str, object]:
    domains: dict[str, int] = {}
    ranges: dict[str, int] = {}
    errors: dict[str, str] = {}
    for name, z in zones.items():
        try:
            v = int((z / "energy_uj").read_text())
            domains[name] = (v // quantum) * quantum if quantum > 1 else v
            ranges[name] = int((z / "max_energy_range_uj").read_text())
        except (OSError, ValueError) as exc:
            errors[name] = type(exc).__name__
    out: dict[str, object] = {"t_ns": time.monotonic_ns(), "domains": domains, "max_energy_range_uj": ranges}
    if errors:
        out["errors"] = errors
    return out


def _groups_of(uid: int, gid: int) -> list[int]:
    return os.getgrouplist(pwd.getpwuid(uid).pw_name, gid)


def is_allowed(uid: int, gid: int, allowed_gid: int, groups_lookup: Callable[[int, int], list[int]] | None) -> bool:
    """root, or primary/supplementary membership in the allowed group."""
    if uid == 0 or gid == allowed_gid:
        return True
    try:
        return allowed_gid in (groups_lookup or _groups_of)(uid, gid)
    except KeyError:
        return False


def peer_allowed(conn: socket.socket, allowed_gid: int) -> bool:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, gid = struct.unpack("3i", creds)
    return is_allowed(uid, gid, allowed_gid, None)


def serve(sock_path: Path, powercap: Path, allowed_gid: int, min_interval: float, quantum: int) -> None:
    zones = discover(powercap)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o117)
    try:
        srv.bind(str(sock_path))
    finally:
        os.umask(old)
    with contextlib.suppress(PermissionError):  # non-root test runs keep the caller's group
        os.chown(sock_path, -1, allowed_gid)
    os.chmod(sock_path, 0o660)
    srv.listen(16)

    def handle(conn: socket.socket) -> None:
        last = 0.0
        with conn:
            if not peer_allowed(conn, allowed_gid):
                conn.sendall(b'{"error":"permission denied"}\n')
                return
            buf = b""
            while True:
                try:
                    data = conn.recv(64)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip() != b"read":
                        conn.sendall(b'{"error":"unknown request"}\n')
                        continue
                    wait = last + min_interval - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    last = time.monotonic()
                    payload = snapshot(zones, quantum) if zones else {"error": "no RAPL zones found"}
                    conn.sendall(json.dumps(payload).encode() + b"\n")

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--socket", default="/run/ailx-rapl/rapl.sock")
    ap.add_argument("--powercap", default="/sys/class/powercap")
    ap.add_argument("--group", default="ailx", help="group allowed to read (besides root)")
    ap.add_argument("--min-interval", type=float, default=0.1, help="seconds between snapshots per connection")
    ap.add_argument("--quantum-uj", type=int, default=1, help="round counters down to this many uJ")
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args()
    try:
        gid = grp.getgrnam(a.group).gr_gid
    except KeyError:
        gid = int(a.group) if a.group.isdigit() else os.getgid()
    serve(Path(a.socket), Path(a.powercap), gid, max(a.min_interval, 0.05), max(a.quantum_uj, 1))


if __name__ == "__main__":
    main()
