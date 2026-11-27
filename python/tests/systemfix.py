# SPDX-License-Identifier: MIT
"""Fixtures for system-layer tests: a writable fake host root and a fake nvidia-smi."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

FAKE_NVIDIA_SMI = r"""#!PYTHON
import json, os, sys
state = os.environ["FAKE_NVSMI_STATE"]
d = json.load(open(state))
args = sys.argv[1:]
idx = args[args.index("-i") + 1] if "-i" in args else None
gpus = [g for g in d["gpus"] if idx is None or str(g["index"]) == idx]
if any(a.startswith("--query-gpu=") for a in args):
    fields = next(a for a in args if a.startswith("--query-gpu=")).split("=", 1)[1].split(",")
    key = {"index": "index", "power.limit": "limit", "power.default_limit": "default",
           "power.min_limit": "min", "power.max_limit": "max"}
    for g in gpus:
        print(", ".join(str(g[key[f]]) for f in fields))
    sys.exit(0)
if "-pl" in args:
    w = float(args[args.index("-pl") + 1])
    for g in gpus:
        if not g["min"] <= w <= g["max"]:
            print("Provided power limit is not a valid power limit"); sys.exit(4)
        g["limit"] = w
    json.dump(d, open(state, "w"))
    print("Power limit set"); sys.exit(0)
sys.exit(2)
"""


def w(root: Path, rel: str, text: str) -> None:
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n")


def build_host(root: Path) -> Path:
    w(root, "/sys/kernel/mm/transparent_hugepage/enabled", "always [madvise] never")
    w(root, "/sys/kernel/mm/transparent_hugepage/defrag", "always defer defer+madvise [madvise] never")
    w(root, "/proc/sys/vm/swappiness", "60")
    w(root, "/proc/sys/kernel/numa_balancing", "0")
    for cpu in range(4):
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/energy_performance_preference", "balance_performance")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor", "powersave")
    w(root, "/sys/bus/platform/drivers/amd_x3d_vcache/AMDI0101:00/amd_x3d_mode", "frequency")
    w(root, "/proc/meminfo", "MemTotal:       65536000 kB\nMemAvailable:   60000000 kB\nHugePages_Total:       0")
    w(root, "/proc/loadavg", "0.10 0.20 0.30 1/100 1234")
    w(root, "/proc/cpuinfo", "processor\t: 0\nvendor_id\t: AuthenticAMD\nmodel name\t: Test CPU\n")
    w(root, "/sys/devices/system/cpu/online", "0-3")
    w(root, "/etc/os-release", 'ID=ubuntu\nVERSION_ID="26.04"\nPRETTY_NAME="Ubuntu 26.04 LTS"')
    w(root, "/proc/sys/kernel/osrelease", "7.0.0-15-generic")
    return root


def install_fake_nvidia_smi(bindir: Path, state: Path, default: float = 450.0) -> Path:
    import json

    bindir.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"gpus": [{"index": 0, "limit": default, "default": default, "min": 150.0, "max": 600.0}]})
    )
    exe = bindir / "nvidia-smi"
    exe.write_text(FAKE_NVIDIA_SMI.replace("#!PYTHON", "#!" + sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.environ["FAKE_NVSMI_STATE"] = str(state)
    return exe
