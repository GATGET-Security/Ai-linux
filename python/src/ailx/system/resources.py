# SPDX-License-Identifier: MIT
"""Available CPU, GPU, RAM and VRAM resources (basis for later resource management).

Static facts come from the existing probe (:func:`ailx.probe.collect_hardware_profile`); this module
only adds the *dynamic* view: what is free now and what limits apply to this process (affinity,
cgroup v2 cpu.max/memory.max), plus live GPU memory from NVML or amdgpu sysfs.
"""

from __future__ import annotations

import os
from typing import Any

from ailx.core import Doc
from ailx.probe import collect_hardware_profile
from ailx.system.paths import Layout


def _meminfo(layout: Layout) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in layout.host("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            parts = v.split()
            out[k] = int(parts[0]) * (1024 if parts[1:] == ["kB"] else 1)
    except (OSError, ValueError):
        pass
    return out


def _cgroup_limits(layout: Layout) -> Doc:
    try:
        rel = next(
            line.split("::", 1)[1]
            for line in layout.host("/proc/self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
    except (OSError, StopIteration):
        return {"cgroup": None}
    base = layout.host("/sys/fs/cgroup") / rel.strip().lstrip("/")

    def rd(name: str) -> str | None:
        try:
            return (base / name).read_text().strip()
        except OSError:
            return None

    mem = rd("memory.max")
    cpu = rd("cpu.max")
    cpus = None
    if cpu and not cpu.startswith("max"):
        quota, period = cpu.split()
        cpus = int(quota) / int(period)
    return {
        "cgroup": rel.strip(),
        "memory_max_bytes": None if mem in (None, "max") else int(mem),
        "cpu_quota_cpus": cpus,
        "memory_current_bytes": int(c) if (c := rd("memory.current")) else None,
    }


def _gpus_live(layout: Layout, use_nvml: bool) -> list[Doc]:
    out: list[Doc] = []
    if use_nvml and str(layout.root) == "/":
        try:
            import pynvml

            pynvml.nvmlInit()
            try:
                for i in range(pynvml.nvmlDeviceGetCount()):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    m = pynvml.nvmlDeviceGetMemoryInfo(h)
                    u = pynvml.nvmlDeviceGetUtilizationRates(h)
                    name = pynvml.nvmlDeviceGetName(h)
                    out.append(
                        {
                            "source": "nvml",
                            "index": i,
                            "name": name.decode() if isinstance(name, bytes) else name,
                            "vram_total_bytes": int(m.total),
                            "vram_used_bytes": int(m.used),
                            "vram_free_bytes": int(m.free),
                            "util_gpu_pct": int(u.gpu),
                        }
                    )
            finally:
                pynvml.nvmlShutdown()
        except Exception:  # driver absent or NVML not installed: reported as no live data
            pass
    for card in sorted(layout.host("/sys/class/drm").glob("card[0-9]*")):
        dev = card / "device"
        try:
            total = int((dev / "mem_info_vram_total").read_text())
            used = int((dev / "mem_info_vram_used").read_text())
        except (OSError, ValueError):
            continue
        out.append(
            {
                "source": "amdgpu-sysfs",
                "card": card.name,
                "vram_total_bytes": total,
                "vram_used_bytes": used,
                "vram_free_bytes": total - used,
            }
        )
    return out


def snapshot(layout: Layout, use_nvml: bool = True) -> Doc:
    hwp = collect_hardware_profile(layout.root, use_nvml=use_nvml)
    mi = _meminfo(layout)
    try:
        allowed = sorted(os.sched_getaffinity(0)) if str(layout.root) == "/" else None
    except OSError:
        allowed = None
    try:
        load = [float(x) for x in layout.host("/proc/loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        load = None
    gpus = _gpus_live(layout, use_nvml)
    res: dict[str, Any] = {
        "hardware_profile_id": hwp["id"],
        "cpu": {
            "logical_cpus": hwp["cpu"]["logical_cpus"],
            "allowed_cpus": len(allowed) if allowed else None,
            "llc_domains": [
                {"id": d["id"], "cpus": d["cpus"], "size_bytes": d["size_bytes"]} for d in hwp["cpu"]["llc_domains"]
            ],
            "loadavg": load,
        },
        "memory": {
            "total_bytes": mi.get("MemTotal"),
            "available_bytes": mi.get("MemAvailable"),
            "swap_total_bytes": mi.get("SwapTotal"),
            "swap_free_bytes": mi.get("SwapFree"),
            "hugepages_total": mi.get("HugePages_Total"),
        },
        "limits": _cgroup_limits(layout),
        "accelerators": [
            {
                "vendor": a["vendor"],
                "pci_address": a["pci_address"],
                "driver": a["driver"],
                "memory_domains": a["memory_domains"],
            }
            for a in hwp["accelerators"]
        ],
        "gpu_live": gpus,
        "unavailable": dict(hwp["unavailable"]),
    }
    if hwp["accelerators"] and not gpus:
        res["unavailable"]["gpu_live"] = "no live GPU memory data (NVML/amdgpu sysfs not available)"
    return res
