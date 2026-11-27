# SPDX-License-Identifier: MIT
"""Hardware capability engine: what this machine can do for AI workloads.

Builds on the probe's hardware profile (same sysfs reader, same fake-root testability) and adds what
placement and tuning need: physical-core topology per LLC domain (CCD), cache sizes, SIMD/AI ISA level,
NUMA nodes with memory, swap/huge pages, accelerator capabilities via providers, compute backends that
are usable *now* (driver stack x installed engine builds), and live free resources.

The document is content-addressed over its static part (``cap-<sha256>``); ``live`` values are
excluded from the ID because they change every call.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ailx.compute import providers
from ailx.core import Doc, canonical_bytes, sha256_hex, utcnow, validate
from ailx.probe.hardware import collect_hardware_profile
from ailx.probe.sysfs import Root, parse_cpu_list, parse_size

# x86-64 micro-architecture levels (psABI) in /proc/cpuinfo flag names
X86_LEVELS = [
    ("x86-64-v4", {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}),
    ("x86-64-v3", {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"}),
    ("x86-64-v2", {"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"}),
]
# Extensions ggml / oneDNN / OpenVINO dispatch on (x86 and Arm names as in /proc/cpuinfo)
AI_EXTENSIONS = [
    "avx",
    "avx2",
    "fma",
    "f16c",
    "avx_vnni",
    "avx512f",
    "avx512bw",
    "avx512vl",
    "avx512_vnni",
    "avx512_bf16",
    "avx512_fp16",
    "amx_tile",
    "amx_bf16",
    "amx_int8",
    "asimd",
    "asimddp",
    "i8mm",
    "bf16",
    "sve",
    "sve2",
]


def isa(flags: list[str]) -> Doc:
    have = set(flags)
    level = None
    for name, req in X86_LEVELS:
        if req <= have:
            level = name
            break
    if level is None and {"sse", "sse2"} <= have:
        level = "x86-64"
    return {"level": level, "ai_extensions": [e for e in AI_EXTENSIONS if e in have]}


def _cpu_topology(root: Root, cpus: list[int]) -> dict[int, Doc]:
    topo: dict[int, Doc] = {}
    for cpu in cpus:
        base = f"/sys/devices/system/cpu/cpu{cpu}"
        caches: dict[str, int | None] = {}
        for idx in root.glob(base + "/cache/index[0-9]*"):
            rel = root.rel(idx)
            lvl, typ = root.read(rel + "/level"), root.read(rel + "/type")
            key = f"l{lvl}" + ("d" if typ == "Data" else "i" if typ == "Instruction" else "")
            caches[key] = parse_size(root.read(rel + "/size") or "")
        topo[cpu] = {
            "core": root.read_int(base + "/topology/core_id"),
            "package": root.read_int(base + "/topology/physical_package_id"),
            "siblings": parse_cpu_list(root.read(base + "/topology/thread_siblings_list") or str(cpu)),
            "max_khz": root.read_int(base + "/cpufreq/cpuinfo_max_freq"),
            "caches": caches,
        }
    return topo


def _numa(root: Root) -> list[Doc]:
    nodes = []
    for n in sorted(root.glob("/sys/devices/system/node/node[0-9]*"), key=lambda p: int(p.name[4:])):
        rel = root.rel(n)
        mem: dict[str, int] = {}
        for line in (root.read(rel + "/meminfo") or "").splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] in ("MemTotal:", "MemFree:"):
                mem[parts[2]] = int(parts[3]) * 1024
        nodes.append(
            {
                "node": int(n.name[4:]),
                "cpus": parse_cpu_list(root.read(rel + "/cpulist") or ""),
                "memory_total_bytes": mem.get("MemTotal:"),
                "memory_free_bytes": mem.get("MemFree:"),
            }
        )
    return nodes


def cpu_capabilities(root: Root, hw_cpu: Doc, numa: list[Doc]) -> Doc:
    cpus = sorted({c for d in hw_cpu["llc_domains"] for c in d["cpus"]}) or list(range(hw_cpu["logical_cpus"]))
    topo = _cpu_topology(root, cpus)
    known_cores = all(t["core"] is not None for t in topo.values())

    def core_key(cpu: int) -> tuple[Any, ...]:
        t = topo[cpu]
        return (t["package"], t["core"]) if known_cores else (min(t["siblings"]),)

    physical = {core_key(c) for c in cpus}
    node_of = {c: n["node"] for n in numa for c in n["cpus"]}
    domains = []
    for d in hw_cpu["llc_domains"]:
        cores = sorted({core_key(c) for c in d["cpus"]})
        primary = sorted({min(c for c in d["cpus"] if core_key(c) == k) for k in cores})  # one CPU per core
        freqs = [topo[c]["max_khz"] for c in d["cpus"] if topo[c]["max_khz"]]
        domains.append(
            {
                "id": d["id"],
                "cpus": d["cpus"],
                "primary_cpus": primary,
                "physical_cores": len(cores),
                "l3_bytes": d["size_bytes"],
                "max_khz": max(freqs) if freqs else None,
                "numa_node": node_of.get(d["cpus"][0]),
                "role": None,
            }
        )
    sizes = [d["l3_bytes"] or 0 for d in domains]
    asym = len(domains) > 1 and max(sizes) >= 2 * max(1, min(sizes))
    if asym:  # e.g. 7950X3D: one V-Cache CCD and one higher-clocked CCD (detection only, no policy)
        for d in domains:
            d["role"] = "large-cache" if (d["l3_bytes"] or 0) == max(sizes) else "frequency"
    first = topo[cpus[0]]["caches"] if cpus else {}
    x3d = root.glob("/sys/bus/platform/drivers/amd_x3d_vcache/*/amd_x3d_mode")
    return {
        "vendor": hw_cpu["vendor"],
        "model": hw_cpu["model"],
        "sockets": hw_cpu["sockets"],
        "physical_cores": len(physical),
        "logical_cpus": len(cpus),
        "smt_active": hw_cpu["smt_active"],
        "isa": isa(hw_cpu["isa_flags"]),
        "caches_per_core_bytes": {"l1d": first.get("l1d"), "l2": first.get("l2")},
        "llc_domains": domains,
        "asymmetric_llc": asym,
        "x3d_mode": root.read(root.rel(x3d[0])) if x3d else None,
        "topology_source": "sysfs core_id"
        if known_cores
        else "assumed one core per logical CPU (no topology in sysfs)",
    }


def _meminfo(root: Root) -> dict[str, int]:
    out = {}
    for line in (root.read("/proc/meminfo") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0].rstrip(":")] = int(parts[1]) * (1024 if len(parts) > 2 and parts[2] == "kB" else 1)
    return out


def _mount_of(root: Root, path: Path) -> Doc:
    """Filesystem holding ``path`` (longest mount-point prefix in /proc/mounts)."""
    best: Doc = {}
    for line in (root.read("/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mp = parts[1].replace("\\040", " ")
        if (str(path) + "/").startswith(mp.rstrip("/") + "/") and len(mp) >= len(best.get("mountpoint", "")):
            best = {"source": parts[0], "mountpoint": mp, "fstype": parts[2]}
    return best


def storage_capabilities(root: Root, models_dir: Path, hw_storage: list[Doc]) -> Doc:
    probe_dir = models_dir
    while not root.path(str(probe_dir)).exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    free = None
    try:
        st = os.statvfs(root.path(str(probe_dir)))
        free = st.f_bavail * st.f_frsize
    except OSError:
        pass
    mnt = _mount_of(root, probe_dir)
    disk = None
    src = mnt.get("source", "")
    for s in hw_storage:
        if src.startswith("/dev/" + s["name"]):
            disk = s
    return {
        "models_dir": str(models_dir),
        "filesystem": mnt or None,
        "device": disk,
        "free_bytes_live": free,
    }


def engines_backends(layout: Any) -> list[Doc]:
    """Installed engine builds and the backend each was built for (from their manifests)."""
    from ailx.system.engines import EngineManager

    variant_backend = {"cpu": "cpu", "cpu-native": "cpu", "cuda": "cuda", "vulkan": "vulkan", "rocm": "rocm"}
    out = []
    try:
        em = EngineManager(layout)
        for inst in em.installs():
            if inst.get("broken"):
                continue
            cur = em.current(inst["engine"]) or {}
            out.append(
                {
                    "engine": inst["engine"],
                    "install_id": inst["install_id"],
                    "variant": inst.get("variant"),
                    "backend": variant_backend.get(str(inst.get("variant")), str(inst.get("variant"))),
                    "current": cur.get("install_id") == inst["install_id"],
                }
            )
    except Exception:  # unreadable engine dir: reported as no engines, never fatal
        pass
    return out


def collect_capabilities(layout: Any, use_nvml: bool = True) -> Doc:
    root = Root(layout.root)
    hwp = collect_hardware_profile(layout.root, use_nvml=use_nvml)
    unavailable: dict[str, str] = dict(hwp["unavailable"])
    live: Doc = {}
    numa = _numa(root)
    mi = _meminfo(root)
    cpu = cpu_capabilities(root, hwp["cpu"], numa)
    devices = providers.discover(root, hwp["accelerators"], live, unavailable, use_nvml)
    host = providers.host_backends(root)
    engines = engines_backends(layout)
    usable = []
    for be in ("cpu", "cuda", "vulkan", "rocm", "sycl"):
        devs = ["cpu"] if be == "cpu" else [d["id"] for d in devices if be in d["backends"]]
        builds = [f"{e['engine']}/{e['install_id']}" for e in engines if e["backend"] == be]
        usable.append(
            {
                "backend": be,
                "devices": devs,
                "host_stack": host[be]["present"],
                "engine_builds": builds,
                "usable": bool(devs) and bool(builds),
            }
        )
    hp_size = mi.get("Hugepagesize")
    memory: Doc = {
        "total_bytes": mi.get("MemTotal"),
        "swap_total_bytes": mi.get("SwapTotal"),
        "hugepages": {"total": mi.get("HugePages_Total"), "size_bytes": hp_size},
        "thp": root.read("/sys/kernel/mm/transparent_hugepage/enabled"),
        "bandwidth": {"bytes_per_s": None, "provenance": "unknown (needs DIMM data or a measurement)"},
    }
    if memory["bandwidth"]["bytes_per_s"] is None:
        unavailable["memory.bandwidth"] = "system memory bandwidth unknown: decode bounds for CPU layers not computed"
    live.update(
        {
            "memory_available_bytes": mi.get("MemAvailable"),
            "swap_free_bytes": mi.get("SwapFree"),
            "hugepages_free": mi.get("HugePages_Free"),
            "loadavg": (root.read("/proc/loadavg") or "").split()[:3],
            "numa_free_bytes": {str(n["node"]): n["memory_free_bytes"] for n in numa},
        }
    )
    storage = storage_capabilities(root, layout.models_dir, hwp["storage"])
    live["models_dir_free_bytes"] = storage.pop("free_bytes_live")
    for n in numa:
        n.pop("memory_free_bytes", None)
    doc: Doc = {
        "schema": "ailx.capabilities/v0",
        "collected_at": utcnow(),
        "hardware_profile_id": hwp["id"],
        "cpu": cpu,
        "numa": numa,
        "memory": memory,
        "accelerators": devices,
        "storage": storage,
        "backends": usable,
        "engines": engines,
        "unavailable": unavailable,
        "live": live,
    }
    return seal_capabilities(doc)


def seal_capabilities(doc: Doc) -> Doc:
    body = {k: v for k, v in doc.items() if k not in ("collected_at", "id", "live")}
    out = dict(doc)
    out["id"] = "cap-" + sha256_hex(canonical_bytes(body))
    validate(out)
    return out


def memory_budget(caps: Doc, reserve_ram_bytes: int, reserve_vram_bytes: int) -> Doc:
    """Usable memory per device after reserves (static totals and live free values)."""
    total = caps["memory"]["total_bytes"] or 0
    avail = caps["live"].get("memory_available_bytes")
    gpus = []
    for d in caps["accelerators"]:
        if d["kind"] != "gpu" or d["memory"]["kind"] != "discrete" or not d["memory"]["total_bytes"]:
            continue
        lv = caps["live"].get(d["id"], {})
        gpus.append(
            {
                "id": d["id"],
                "name": d["name"],
                "total_bytes": d["memory"]["total_bytes"],
                "usable_bytes": max(0, d["memory"]["total_bytes"] - reserve_vram_bytes),
                "free_bytes_live": lv.get("vram_free_bytes"),
                "bandwidth": d["memory"]["bandwidth"],
                "backends": d["backends"],
            }
        )
    return {
        "ram_total_bytes": total,
        "ram_usable_bytes": max(0, total - reserve_ram_bytes),
        "ram_available_bytes_live": avail,
        "gpus": gpus,
        "reserve": {"ram_bytes": reserve_ram_bytes, "vram_bytes": reserve_vram_bytes},
    }
