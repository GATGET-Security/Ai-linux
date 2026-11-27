#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ailx-apply-helper: the only privileged writer of the AI Linux system layer.

Reads exactly one JSON request from stdin and writes one JSON response line to stdout:

  {"op": "write", "target": "/sys/kernel/mm/transparent_hugepage/enabled", "value": "madvise"}
  {"op": "write", "target": "nvidia:0", "value": "315"}          # watts; reductions only
  {"op": "allowlist"}                                             # introspection (no privilege needed)

Security properties:
* Independent allow-list of targets (exact paths or tight regexes) and values (regexes); the
  caller's knob registry is not trusted.
* Paths must be absolute, without ``..``; after resolving symlinks they must stay below /sys or
  /proc/sys. Values are short, single-line and must match the allow-list.
* GPU power limits: only ``min_limit <= W <= default_limit`` (lowering is allowed, raising above
  the vendor default is not).
* No shell, no command-line arguments, no environment-controlled behaviour in production: the test
  overrides ``AILX_HELPER_TEST_ROOT`` / ``AILX_HELPER_TEST_NVIDIA_SMI`` are ignored when invoked via
  sudo or pkexec.
* Standard library only, so it can be reviewed in isolation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

VERSION = "0.1.0"
CPU = r"/sys/devices/system/cpu/cpu[0-9]{1,4}/cpufreq/"
ALLOW: list[tuple[str, str]] = [
    (r"/sys/kernel/mm/transparent_hugepage/enabled", r"always|madvise|never"),
    (r"/sys/kernel/mm/transparent_hugepage/defrag", r"always|defer|defer\+madvise|madvise|never"),
    (r"/proc/sys/vm/swappiness", r"[0-9]|[1-9][0-9]|1[0-9][0-9]|200"),
    (r"/proc/sys/kernel/numa_balancing", r"0|1"),
    (CPU + "energy_performance_preference", r"default|performance|balance_performance|balance_power|power"),
    (CPU + "scaling_governor", r"performance|powersave|schedutil|ondemand|conservative"),
    (r"/sys/bus/platform/drivers/amd_x3d_vcache/[A-Za-z0-9:._-]{1,64}/amd_x3d_mode", r"frequency|cache"),
]


def _test_overrides_allowed() -> bool:
    return not any(k in os.environ for k in ("SUDO_UID", "PKEXEC_UID"))


def _root() -> str:
    if _test_overrides_allowed():
        return os.environ.get("AILX_HELPER_TEST_ROOT", "/")
    return "/"


def check_file_target(target: str, value: str) -> str:
    if not target.startswith("/") or ".." in target.split("/") or "\x00" in target:
        raise PermissionError("target must be an absolute normalised path")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,32}", value):
        raise PermissionError("value has invalid characters or length")
    for path_re, value_re in ALLOW:
        if re.fullmatch(path_re, target):
            if not re.fullmatch(value_re, value):
                raise PermissionError(f"value {value!r} not allowed for {target}")
            break
    else:
        raise PermissionError(f"target {target} not in allow-list")
    root = _root()
    host = os.path.join(root, target.lstrip("/"))
    real = os.path.realpath(host)
    base = os.path.realpath(root)
    rel = "/" + os.path.relpath(real, base) if real.startswith(base) else real
    if not (rel.startswith("/sys/") or rel.startswith("/proc/sys/")):
        raise PermissionError(f"{target} resolves outside /sys or /proc/sys")
    return host


def nvidia_smi() -> str:
    if _test_overrides_allowed() and "AILX_HELPER_TEST_NVIDIA_SMI" in os.environ:
        return os.environ["AILX_HELPER_TEST_NVIDIA_SMI"]
    for p in ("/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi"):
        if os.path.exists(p):
            return p
    raise FileNotFoundError("nvidia-smi not found")


def write_power_limit(index: str, value: str) -> None:
    if not re.fullmatch(r"[0-9]{1,2}", index) or not re.fullmatch(r"[0-9]{2,4}", value):
        raise PermissionError("invalid GPU index or wattage")
    exe = nvidia_smi()
    q = subprocess.run(
        [exe, "-i", index, "--query-gpu=power.min_limit,power.default_limit", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    lo, default = (float(x) for x in q.stdout.strip().split(","))
    watts = float(value)
    if not lo <= watts <= default:
        raise PermissionError(f"{watts} W outside [{lo}, {default}] (raising above default is not allowed)")
    subprocess.run([exe, "-i", index, "-pl", value], capture_output=True, text=True, timeout=30, check=True)


def handle(req: dict[str, object]) -> dict[str, object]:
    op = req.get("op")
    if op == "allowlist":
        return {"ok": True, "version": VERSION, "allow": [{"target": t, "value": v} for t, v in ALLOW]}
    if op != "write":
        raise PermissionError(f"unknown op {op!r}")
    target, value = str(req.get("target", "")), str(req.get("value", ""))
    if target.startswith("nvidia:"):
        write_power_limit(target.split(":", 1)[1], value)
    else:
        with open(check_file_target(target, value), "w") as f:
            f.write(value)
    return {"ok": True, "target": target, "value": value}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read(4096))
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
        resp = handle(req)
        code = 0
    except (PermissionError, ValueError, OSError, subprocess.SubprocessError) as exc:
        resp, code = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 1
    print(json.dumps(resp))
    caller = os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_UID") or str(os.getuid())
    print(
        f"ailx-apply-helper: caller={caller} request={json.dumps(req) if code == 0 else '-'} ok={code == 0}",
        file=sys.stderr,
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
