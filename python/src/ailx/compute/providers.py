# SPDX-License-Identifier: MIT
"""Accelerator providers (hardware-abstraction.md, provider interface v0, read-only part).

Each provider discovers its devices and reports normalized capabilities. Values carry provenance:
``driver`` (NVML / nvidia-smi / sysfs), ``datasheet:<source-id>`` or ``theoretical`` (formula shown).
Unknown values stay ``None`` and are listed in ``unavailable`` — never guessed.

Providers: ``nvidia`` (NVML, then nvidia-smi, then sysfs), ``amd`` (amdgpu sysfs), ``intel`` (i915/xe
sysfs), ``npu`` (DRM accel subsystem). New device classes add a provider; nothing else changes.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from ailx.core import Doc
from ailx.probe.sysfs import Root

GiB = 1024**3
LIB_DIRS = ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")
VULKAN_ICD_DIRS = ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d")

# Datasheet memory bandwidth for devices in the test plan (bytes/s). Source IDs resolve in
# docs/research/03-hardware-io-and-model-loading.md. Everything else: formula from driver values.
DATASHEET_BW = {
    "NVIDIA GeForce RTX 4090": (1008e9, "datasheet:H-12"),
}


def _has_lib(root: Root, name: str) -> bool:
    return any(root.glob(f"{d}/{name}*") for d in LIB_DIRS)


def _vulkan_icds(root: Root) -> list[str]:
    return sorted({p.name for d in VULKAN_ICD_DIRS for p in root.glob(f"{d}/*.json")})


def _num(x: str | None) -> float | None:
    if x is None:
        return None
    x = x.strip()
    if not x or x.startswith("[") or x.lower() in ("n/a", "not supported"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def theoretical_bw(mem_clock_mhz: float | None, bus_width_bits: float | None) -> float | None:
    """*Theoretical* DRAM bandwidth = 2 (double data rate) × max memory clock × bus width / 8."""
    if not mem_clock_mhz or not bus_width_bits:
        return None
    return 2 * mem_clock_mhz * 1e6 * bus_width_bits / 8


def _bandwidth(model: str | None, clock: float | None, bus: float | None) -> Doc:
    if model in DATASHEET_BW:
        bw, prov = DATASHEET_BW[model]
        return {"bytes_per_s": bw, "provenance": prov, "kind": "theoretical"}
    bw2 = theoretical_bw(clock, bus)
    if bw2:
        return {
            "bytes_per_s": bw2,
            "provenance": "theoretical: 2 x max_mem_clock x bus_width / 8 (driver-reported inputs)",
            "kind": "theoretical",
        }
    return {"bytes_per_s": None, "provenance": "unknown", "kind": None}


def _device(vendor: str, kind: str, **kw: Any) -> Doc:
    base: Doc = {
        "vendor": vendor,
        "kind": kind,  # gpu | npu
        "index": None,
        "name": None,
        "pci_address": None,
        "driver": None,
        "driver_version": None,
        "compute_capability": None,
        "memory": {"kind": "unknown", "total_bytes": None, "bandwidth": _bandwidth(None, None, None)},
        "host_link": None,
        "power_limit_default_w": None,
        "backends": [],
        "source": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- NVIDIA


def _nvml(live: Doc) -> list[Doc]:
    try:
        import pynvml
    except ImportError:
        return []
    try:
        pynvml.nvmlInit()
    except Exception:
        return []
    out: list[Doc] = []
    try:

        def s(x: Any) -> str:
            return x.decode() if isinstance(x, bytes) else str(x)

        drv = s(pynvml.nvmlSystemGetDriverVersion())
        try:
            cuda = pynvml.nvmlSystemGetCudaDriverVersion()
            cuda_s: str | None = f"{cuda // 1000}.{(cuda % 1000) // 10}"
        except Exception:
            cuda_s = None
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = s(pynvml.nvmlDeviceGetName(h))
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            opt: dict[str, Any] = {}
            for key, fn in (
                ("cc", lambda h=h: pynvml.nvmlDeviceGetCudaComputeCapability(h)),
                ("bus", lambda h=h: pynvml.nvmlDeviceGetMemoryBusWidth(h)),
                ("clock", lambda h=h: pynvml.nvmlDeviceGetMaxClockInfo(h, pynvml.NVML_CLOCK_MEM)),
                ("gen", lambda h=h: pynvml.nvmlDeviceGetMaxPcieLinkGeneration(h)),
                ("width", lambda h=h: pynvml.nvmlDeviceGetMaxPcieLinkWidth(h)),
                ("pl", lambda h=h: pynvml.nvmlDeviceGetPowerManagementDefaultLimit(h) / 1000),
            ):
                try:
                    opt[key] = fn()  # type: ignore[no-untyped-call]
                except Exception:
                    opt[key] = None
            cc = opt["cc"]
            out.append(
                _device(
                    "nvidia",
                    "gpu",
                    index=i,
                    name=name,
                    pci_address=s(pynvml.nvmlDeviceGetPciInfo(h).busId).lower()[-12:],
                    driver="nvidia",
                    driver_version=drv,
                    cuda_driver_version=cuda_s,
                    compute_capability=f"{cc[0]}.{cc[1]}" if cc else None,
                    memory={
                        "kind": "discrete",
                        "total_bytes": int(mem.total),
                        "bus_width_bits": opt["bus"],
                        "max_clock_mhz": opt["clock"],
                        "bandwidth": _bandwidth(name, opt["clock"], opt["bus"]),
                    },
                    host_link={"max_gen": opt["gen"], "max_width": opt["width"]},
                    power_limit_default_w=opt["pl"],
                    source="nvml",
                )
            )
            live[f"nvidia:{i}"] = {"vram_free_bytes": int(mem.free), "vram_used_bytes": int(mem.used)}
    finally:
        pynvml.nvmlShutdown()
    return out


SMI_CORE = ["index", "name", "pci.bus_id", "memory.total", "memory.used", "memory.free", "driver_version"]
SMI_EXT = ["compute_cap", "clocks.max.memory", "power.default_limit", "pcie.link.gen.max", "pcie.link.width.max"]


def _smi(fields: list[str]) -> list[list[str]] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    rows = [[c.strip() for c in line.split(",")] for line in r.stdout.strip().splitlines()]
    return [row for row in rows if len(row) == len(fields)]


def _nvidia_smi(live: Doc, unavailable: dict[str, str]) -> list[Doc]:
    core = _smi(SMI_CORE)
    if not core:
        return []
    ext = _smi(SMI_EXT)
    if ext is None:
        unavailable["nvidia.extended"] = "nvidia-smi does not support compute_cap/clock/link fields"
    out = []
    for n, row in enumerate(core):
        c = dict(zip(SMI_CORE, row, strict=True))
        e = dict(zip(SMI_EXT, ext[n], strict=True)) if ext and n < len(ext) else {}
        mib = 1024**2
        total, used, free = (_num(c[k]) for k in ("memory.total", "memory.used", "memory.free"))
        clock = _num(e.get("clocks.max.memory"))
        idx = int(_num(c["index"]) or n)
        out.append(
            _device(
                "nvidia",
                "gpu",
                index=idx,
                name=c["name"],
                pci_address=c["pci.bus_id"].lower()[-12:],
                driver="nvidia",
                driver_version=c["driver_version"],
                compute_capability=e.get("compute_cap") or None,
                memory={
                    "kind": "discrete",
                    "total_bytes": int(total * mib) if total is not None else None,
                    "bus_width_bits": None,
                    "max_clock_mhz": clock,
                    "bandwidth": _bandwidth(c["name"], clock, None),
                },
                host_link={
                    "max_gen": _num(e.get("pcie.link.gen.max")),
                    "max_width": _num(e.get("pcie.link.width.max")),
                },
                power_limit_default_w=_num(e.get("power.default_limit")),
                source="nvidia-smi",
            )
        )
        if free is not None:
            live[f"nvidia:{idx}"] = {
                "vram_free_bytes": int(free * mib),
                "vram_used_bytes": int((used or 0) * mib),
            }
    return out


def nvidia(root: Root, hw_accs: list[Doc], live: Doc, unavailable: dict[str, str], use_nvml: bool) -> list[Doc]:
    devs: list[Doc] = []
    if root.is_live():
        devs = (_nvml(live) if use_nvml else []) or _nvidia_smi(live, unavailable)
    if not devs:  # sysfs only: the device exists, but its memory and driver stack are unknown
        for i, acc in enumerate(a for a in hw_accs if a["vendor"] == "nvidia"):
            devs.append(
                _device(
                    "nvidia",
                    "gpu",
                    index=i,
                    name=acc.get("model"),
                    pci_address=acc.get("pci_address"),
                    driver=acc.get("driver"),
                    driver_version=root.read("/sys/module/nvidia/version"),
                    host_link=acc.get("pcie"),
                    source="sysfs",
                )
            )
            unavailable[f"nvidia:{i}.memory"] = "no NVML/nvidia-smi: VRAM size and bandwidth unknown"
    has_kmod = root.exists("/proc/driver/nvidia/version") or root.exists("/sys/module/nvidia")
    cuda_ok = has_kmod and _has_lib(root, "libcuda.so")
    for d in devs:
        d["backends"] = [b for b, ok in (("cuda", cuda_ok), ("vulkan", _vk(root, "nvidia"))) if ok]
        if not cuda_ok:
            unavailable[f"nvidia:{d['index']}.cuda"] = "CUDA backend unavailable: " + (
                "no libcuda.so" if has_kmod else "NVIDIA kernel module not loaded"
            )
    return devs


def _vk(root: Root, needle: str) -> bool:
    return _has_lib(root, "libvulkan.so") and any(needle in n for n in _vulkan_icds(root))


# ---------------------------------------------------------------- AMD / Intel / NPU (sysfs)


def _sysfs_gpus(root: Root, hw_accs: list[Doc], vendor: str) -> list[Doc]:
    out = []
    for i, acc in enumerate(a for a in hw_accs if a["vendor"] == vendor):
        dom = (acc.get("memory_domains") or [{}])[0]
        out.append(
            _device(
                vendor,
                "gpu",
                index=i,
                name=acc.get("model") or acc.get("device_id"),
                pci_address=acc.get("pci_address"),
                driver=acc.get("driver"),
                memory={
                    "kind": dom.get("kind", "unknown"),
                    "total_bytes": dom.get("size_bytes"),
                    "bandwidth": _bandwidth(None, None, None),
                },
                host_link=acc.get("pcie"),
                source="sysfs",
            )
        )
    return out


def amd(root: Root, hw_accs: list[Doc], live: Doc, unavailable: dict[str, str], use_nvml: bool) -> list[Doc]:
    devs = _sysfs_gpus(root, hw_accs, "amd")
    rocm = root.exists("/dev/kfd") and (root.exists("/opt/rocm") or _has_lib(root, "libamdhip64.so"))
    for d in devs:
        d["backends"] = [b for b, ok in (("rocm", rocm), ("vulkan", _vk(root, "radeon"))) if ok]
        if d["memory"]["bandwidth"]["bytes_per_s"] is None:
            unavailable[f"amd:{d['index']}.bandwidth"] = "memory bandwidth not reported by amdgpu sysfs"
    return devs


def intel(root: Root, hw_accs: list[Doc], live: Doc, unavailable: dict[str, str], use_nvml: bool) -> list[Doc]:
    devs = [d for d in _sysfs_gpus(root, hw_accs, "intel") if d["driver"] in ("i915", "xe", None)]
    l0 = _has_lib(root, "libze_loader.so")
    for d in devs:
        if d["memory"]["kind"] == "unknown":
            d["memory"]["kind"] = "unified"  # iGPU without local memory: shares system RAM
        d["backends"] = [b for b, ok in (("sycl", l0), ("vulkan", _vk(root, "intel"))) if ok]
    return devs


def npu(root: Root, hw_accs: list[Doc], live: Doc, unavailable: dict[str, str], use_nvml: bool) -> list[Doc]:
    out = []
    for i, a in enumerate(root.glob("/sys/class/accel/accel[0-9]*")):
        dev = root.rel(a / "device")
        out.append(
            _device(
                "other",
                "npu",
                index=i,
                name=a.name,
                pci_address=root.readlink_name(dev),
                driver=root.readlink_name(dev + "/driver"),
                source="sysfs",
            )
        )
        unavailable[f"npu:{i}.engine"] = "no managed engine supports NPUs yet (hardware-abstraction.md)"
    return out


Provider = Callable[[Root, list[Doc], Doc, dict[str, str], bool], list[Doc]]
PROVIDERS: dict[str, Provider] = {"nvidia": nvidia, "amd": amd, "intel": intel, "npu": npu}


def discover(root: Root, hw_accs: list[Doc], live: Doc, unavailable: dict[str, str], use_nvml: bool) -> list[Doc]:
    devices: list[Doc] = []
    for name, prov in PROVIDERS.items():
        for d in prov(root, hw_accs, live, unavailable, use_nvml):
            d["id"] = f"{name}:{d['index']}"
            devices.append(d)
    return devices


def host_backends(root: Root) -> dict[str, Doc]:
    """Userspace compute stacks present on the host (independent of devices)."""
    return {
        "cpu": {"present": True, "detail": "always available"},
        "cuda": {"present": _has_lib(root, "libcuda.so"), "detail": "libcuda.so (NVIDIA driver userspace)"},
        "vulkan": {"present": _has_lib(root, "libvulkan.so"), "detail": ", ".join(_vulkan_icds(root)) or "no ICDs"},
        "rocm": {"present": root.exists("/dev/kfd") and root.exists("/opt/rocm"), "detail": "/dev/kfd + /opt/rocm"},
        "sycl": {"present": _has_lib(root, "libze_loader.so"), "detail": "Level Zero loader"},
    }
