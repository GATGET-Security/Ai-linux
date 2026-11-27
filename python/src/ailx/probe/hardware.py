# SPDX-License-Identifier: MIT
"""Hardware profile collection (read-only, unprivileged where possible)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ailx.core import Doc, canonical_bytes, sha256_hex, utcnow, validate
from ailx.probe.sysfs import Root, parse_cpu_list, parse_size

PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def _cpuinfo(root: Root) -> list[dict[str, str]]:
    text = root.read("/proc/cpuinfo") or ""
    procs: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                procs.append(cur)
                cur = {}
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            cur[k.strip()] = v.strip()
    if cur:
        procs.append(cur)
    return procs


def collect_cpu(root: Root, unavailable: dict[str, str]) -> Doc:
    procs = _cpuinfo(root)
    first = procs[0] if procs else {}
    online = root.read("/sys/devices/system/cpu/online")
    cpus = (
        parse_cpu_list(online)
        if online
        else sorted(int(p.name[3:]) for p in root.glob("/sys/devices/system/cpu/cpu[0-9]*"))
    )
    if not cpus:
        cpus = list(range(len(procs))) or [0]
        unavailable["cpu.logical_cpus"] = "no /sys cpu topology; derived from /proc/cpuinfo"
    smt = root.read("/sys/devices/system/cpu/smt/active")
    sockets = {p.get("physical id") for p in procs if "physical id" in p}

    domains: dict[tuple[int, ...], Doc] = {}
    for cpu in cpus:
        for idx in root.glob(f"/sys/devices/system/cpu/cpu{cpu}/cache/index[0-9]*"):
            rel = root.rel(idx)
            if root.read(rel + "/level") != "3":
                continue
            shared = root.read(rel + "/shared_cpu_list")
            size = root.read(rel + "/size")
            if shared is None:
                continue
            key = tuple(parse_cpu_list(shared))
            domains.setdefault(key, {"cpus": list(key), "size_bytes": parse_size(size or ""), "level": 3})
    llc = [dict(d, id=i) for i, d in enumerate(sorted(domains.values(), key=lambda d: d["cpus"][0]))]
    if not llc:
        unavailable["cpu.llc_domains"] = "no L3 cache information in sysfs"
    if not procs:
        unavailable["cpu.model"] = "/proc/cpuinfo not readable"
    return {
        "vendor": first.get("vendor_id"),
        "model": first.get("model name"),
        "sockets": len(sockets) if sockets else None,
        "logical_cpus": len(cpus),
        "smt_active": None if smt is None else smt == "1",
        "llc_domains": llc,
        "isa_flags": sorted(set(first.get("flags", "").split())),
    }


def collect_memory(root: Root, unavailable: dict[str, str]) -> Doc:
    total = None
    for line in (root.read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
    if total is None:
        unavailable["memory.total_bytes"] = "/proc/meminfo not readable"
    nodes = root.glob("/sys/devices/system/node/node[0-9]*")
    return {"total_bytes": total, "numa_nodes": len(nodes) if nodes else None}


def _nvml_devices() -> dict[str, Doc]:
    """NVML enrichment keyed by PCI bus id (lower-case, 'dddd:bb:dd.f'). Empty if unavailable."""
    try:
        import pynvml
    except ImportError:
        return {}
    out: dict[str, Doc] = {}
    try:
        pynvml.nvmlInit()
    except Exception:
        return {}
    try:
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            bus = pynvml.nvmlDeviceGetPciInfo(h).busId
            bus = bus.decode() if isinstance(bus, bytes) else bus
            name = pynvml.nvmlDeviceGetName(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h).total
            key = bus.lower()[-12:]
            out[key] = {"model": name.decode() if isinstance(name, bytes) else name, "memory_total": int(mem)}
    finally:
        pynvml.nvmlShutdown()
    return out


def collect_accelerators(root: Root, unavailable: dict[str, str], use_nvml: bool) -> list[Doc]:
    nvml = _nvml_devices() if (use_nvml and root.is_live()) else {}
    seen: set[str] = set()
    accs: list[Doc] = []
    for card in root.glob("/sys/class/drm/card[0-9]*"):
        if "-" in card.name:  # connectors such as card0-DP-1
            continue
        dev = root.rel(card / "device")
        vendor_id = root.read(dev + "/vendor")
        if vendor_id is None:
            continue
        pci = root.readlink_name(dev) or root.read(dev + "/uevent_pci")
        if pci in seen:
            continue
        seen.add(pci or card.name)
        vendor = PCI_VENDORS.get(vendor_id, "other")
        driver = root.readlink_name(dev + "/driver")
        vram = root.read_int(dev + "/mem_info_vram_total")
        model = None
        domains: list[Doc] = []
        enrich = nvml.get((pci or "").lower()[-12:])
        if enrich:
            model = enrich["model"]
            domains.append({"kind": "discrete", "size_bytes": enrich["memory_total"], "provenance": "driver"})
        elif vram:
            # amdgpu reports VRAM; tiny carve-outs on APUs indicate unified memory (GTT/TTM-limited)
            kind = "discrete" if vram >= 2 * 1024**3 else "unified"
            domains.append(
                {
                    "kind": kind,
                    "size_bytes": vram,
                    "provenance": "sysfs",
                    "limit_source": None if kind == "discrete" else "ttm/gtt (see hardware-abstraction.md)",
                }
            )
        else:
            domains.append({"kind": "unknown", "size_bytes": None, "provenance": "sysfs"})
            unavailable[f"accelerators.{pci}.memory"] = "no VRAM info in sysfs" + (
                "" if root.is_live() and use_nvml else "; NVML not used"
            )
        pcie = {
            "current_speed": root.read(dev + "/current_link_speed"),
            "current_width": root.read_int(dev + "/current_link_width"),
            "max_speed": root.read(dev + "/max_link_speed"),
            "max_width": root.read_int(dev + "/max_link_width"),
        }
        accs.append(
            {
                "vendor": vendor,
                "vendor_id": vendor_id,
                "device_id": root.read(dev + "/device"),
                "model": model,
                "arch": None,
                "pci_address": pci,
                "driver": driver,
                "pcie": pcie if any(v is not None for v in pcie.values()) else None,
                "memory_domains": domains,
            }
        )
    if use_nvml and root.is_live() and not nvml:
        unavailable["accelerators.nvml"] = "NVML not available (no NVIDIA driver/GPU or nvidia-ml-py missing)"
    return accs


def collect_storage(root: Root) -> list[Doc]:
    out: list[Doc] = []
    for blk in root.glob("/sys/block/*"):
        name = blk.name
        if name.startswith(("loop", "ram", "zram", "dm-", "sr", "md")):
            continue
        rel = f"/sys/block/{name}"
        sectors = root.read_int(rel + "/size")
        link = None
        transport = "nvme" if name.startswith("nvme") else ("virtio" if name.startswith("vd") else None)
        if name.startswith("nvme"):
            m = re.match(r"(nvme\d+)", name)
            ctrl = m.group(1) if m else name
            base = f"/sys/class/nvme/{ctrl}/device"
            link = {
                "current_speed": root.read(base + "/current_link_speed"),
                "current_width": root.read_int(base + "/current_link_width"),
            }
        rot = root.read(rel + "/queue/rotational")
        out.append(
            {
                "name": name,
                "model": root.read(rel + "/device/model"),
                "transport": transport,
                "size_bytes": sectors * 512 if sectors is not None else None,
                "rotational": None if rot is None else rot == "1",
                "link": link,
            }
        )
    return out


def collect_power_interfaces(root: Root) -> list[str]:
    names = []
    for z in root.glob("/sys/class/powercap/*"):
        n = root.read(root.rel(z) + "/name")
        if n:
            names.append(f"powercap:{z.name}:{n}")
    if root.exists("/proc/driver/nvidia/version"):
        names.append("nvml")
    return sorted(names)


def collect_hardware_profile(base: str | Path = "/", use_nvml: bool = True) -> Doc:
    root = Root(base)
    unavailable: dict[str, str] = {}
    doc: dict[str, Any] = {
        "schema": "ailx.hardware-profile/v0",
        "collected_at": utcnow(),
        "cpu": collect_cpu(root, unavailable),
        "memory": collect_memory(root, unavailable),
        "accelerators": collect_accelerators(root, unavailable, use_nvml),
        "storage": collect_storage(root),
        "power_interfaces": collect_power_interfaces(root),
        "unavailable": unavailable,
    }
    return seal_profile(doc)


def seal_profile(doc: Doc) -> Doc:
    """IDs must not depend on the collection time: hash without ``collected_at``."""
    body = {k: v for k, v in doc.items() if k not in ("collected_at", "id")}
    out = dict(doc)
    out["id"] = "hwp-" + sha256_hex(canonical_bytes(body))
    validate(out)
    return out


__all__ = ["collect_hardware_profile", "seal_profile"]
