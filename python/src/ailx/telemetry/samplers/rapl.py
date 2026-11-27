# SPDX-License-Identifier: MIT
"""RAPL client sampler. Reads aggregated counters from the privileged ``ailx-rapl-collector``.

The harness itself never reads ``energy_uj`` directly and never changes its permissions
(PLATYPUS side channel; [K-50]). Without the helper, the sampler reports ``available: false``.
"""

from __future__ import annotations

import json
import os
import socket
import sys

from ailx.telemetry.protocol import header, parse_interval, run_loop

DEFAULT_SOCKET = "/run/ailx-rapl/rapl.sock"


def query(path: str) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(path)
        s.sendall(b"read\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    data: dict[str, object] = json.loads(buf)
    return data


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    interval = max(parse_interval(argv, 0.2), 0.1)
    path = os.environ.get("AILX_RAPL_SOCKET", DEFAULT_SOCKET)
    try:
        first = query(path)
    except (OSError, ValueError) as exc:
        header(
            "rapl",
            "0.1.0",
            [],
            interval,
            available=False,
            reason=f"RAPL helper not reachable at {path}: {type(exc).__name__}",
        )
        return
    if "error" in first:
        header("rapl", "0.1.0", [], interval, available=False, reason=str(first["error"]))
        return
    domains = first.get("domains", {})
    wraps = first.get("max_energy_range_uj", {})
    assert isinstance(domains, dict) and isinstance(wraps, dict)
    metrics = [
        {"name": f"rapl.{d}.energy_uj", "unit": "uJ", "type": "counter", "wrap": float(wraps.get(d, 0))}
        for d in domains
    ]
    header("rapl", "0.1.0", metrics, interval)

    def read() -> dict[str, float | None]:
        try:
            snap = query(path)
        except (OSError, ValueError):
            return {str(m["name"]): None for m in metrics}
        doms = snap.get("domains", {})
        assert isinstance(doms, dict)
        return {f"rapl.{d}.energy_uj": float(v) for d, v in doms.items()}

    run_loop(read, interval)


if __name__ == "__main__":
    main()
