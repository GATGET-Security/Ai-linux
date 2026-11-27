# SPDX-License-Identifier: MIT
"""Environment fingerprint: software/configuration state that can influence a measurement."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ailx import __version__
from ailx.core import Doc, file_sha256, seal
from ailx.probe.sysfs import Root, bracketed

TUNING_DAEMONS = (
    "tuned",
    "tuned-ppd",
    "power-profiles-daemon",
    "irqbalance",
    "thermald",
    "scx_loader",
    "scx",
    "system76-power",
    "auto-cpufreq",
    "tlp",
    "gamemoded",
    "nvidia-persistenced",
    "unattended-upgrades",
)
SECUREBOOT_VAR = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"


def _os_release(root: Root) -> Doc:
    data: dict[str, str] = {}
    for line in (root.read("/etc/os-release") or root.read("/usr/lib/os-release") or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip().strip('"')
    return {"id": data.get("ID"), "version_id": data.get("VERSION_ID"), "pretty_name": data.get("PRETTY_NAME")}


def _secure_boot(root: Root) -> bool | None:
    try:
        raw = root.path(SECUREBOOT_VAR).read_bytes()
    except OSError:
        return None
    return bool(raw[-1]) if raw else None


def _kernel_config(root: Root, release: str) -> tuple[str | None, dict[str, str]]:
    path = root.path(f"/boot/config-{release}")
    if not path.is_file():
        return None, {}
    opts: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            k, v = line.split("=", 1)
            opts[k] = v
    return file_sha256(path), opts


def _services(root: Root, unavailable: dict[str, str]) -> dict[str, str]:
    if not root.is_live():
        return {}
    if shutil.which("systemctl") is None:
        unavailable["services.tuning_daemons"] = "systemctl not available"
        return {}
    out: dict[str, str] = {}
    for name in TUNING_DAEMONS:
        try:
            r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        state = r.stdout.strip() or "unknown"
        if state not in ("inactive", "unknown"):
            out[name] = state
    return out


def _nvml_state(unavailable: dict[str, str]) -> list[Doc]:
    try:
        import pynvml
    except ImportError:
        unavailable["accelerator_state"] = "nvidia-ml-py not installed"
        return []
    try:
        pynvml.nvmlInit()
    except Exception as exc:  # NVMLError has many subclasses
        unavailable["accelerator_state"] = f"NVML init failed: {type(exc).__name__}"
        return []
    out: list[Doc] = []
    try:
        drv = pynvml.nvmlSystemGetDriverVersion()
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            st: dict[str, Any] = {"index": i, "driver_version": drv.decode() if isinstance(drv, bytes) else drv}
            for key, fn in (
                ("power_limit_mw", pynvml.nvmlDeviceGetPowerManagementLimit),
                ("default_power_limit_mw", pynvml.nvmlDeviceGetPowerManagementDefaultLimit),
                ("persistence_mode", pynvml.nvmlDeviceGetPersistenceMode),
                ("pcie_gen", pynvml.nvmlDeviceGetCurrPcieLinkGeneration),
                ("pcie_width", pynvml.nvmlDeviceGetCurrPcieLinkWidth),
            ):
                try:
                    st[key] = int(fn(h))
                except Exception as exc:
                    st[key] = None
                    unavailable[f"accelerator_state.{i}.{key}"] = type(exc).__name__
            out.append(st)
    finally:
        pynvml.nvmlShutdown()
    return out


class HashCache:
    """sha256 cache keyed by (device, inode, size, mtime_ns) so large model files are hashed once."""

    def __init__(self, path: Path | None = None) -> None:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        self.path = path or base / "ailx" / "hashes.json"
        try:
            self.data: dict[str, str] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def sha256(self, file: str | Path) -> str:
        st = os.stat(file)
        key = f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}"
        if key not in self.data:
            self.data[key] = file_sha256(file)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data))
            except OSError:
                pass
        return self.data[key]


def _git_sha() -> str | None:
    here = Path(__file__).resolve().parent
    try:
        r = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def collect_fingerprint(
    hardware_profile_id: str,
    base: str | Path = "/",
    models: Iterable[str | Path] = (),
    runtime_bundles: Iterable[Doc] = (),
    use_nvml: bool = True,
    hash_cache: HashCache | None = None,
    platform_state: Doc | None = None,
) -> Doc:
    root = Root(base)
    unavailable: dict[str, str] = {}
    release = root.read("/proc/sys/kernel/osrelease") or (platform.release() if root.is_live() else "unknown")
    cfg_sha, cfg = _kernel_config(root, release)
    if cfg_sha is None:
        unavailable["kernel.config_sha256"] = f"/boot/config-{release} not found"

    mitigations = {
        p.name: (root.read(root.rel(p)) or "") for p in root.glob("/sys/devices/system/cpu/vulnerabilities/*")
    }
    lockdown = bracketed(root.read("/sys/kernel/security/lockdown"))
    if lockdown is None:
        unavailable["security.lockdown"] = "securityfs lockdown file not readable"
    secure_boot = _secure_boot(root)
    if secure_boot is None:
        unavailable["security.secure_boot"] = "EFI SecureBoot variable not readable (legacy boot or no efivarfs)"
    iommu = root.glob("/sys/kernel/iommu_groups/*")

    ext_state = root.read("/sys/kernel/sched_ext/state")
    sched: dict[str, Any] = {
        "ext_state": ext_state,
        "ext_ops": root.read("/sys/kernel/sched_ext/root/ops"),
        "sched_ext_available": root.exists("/sys/kernel/sched_ext"),
        "sched_cache_config": cfg.get("CONFIG_SCHED_CACHE"),
        "sched_cache_enabled": root.read("/sys/kernel/debug/sched/llc_balancing/enabled"),
        "preempt": bracketed(root.read("/sys/kernel/debug/sched/preempt")),
        "preempt_config": next(
            (
                k
                for k in (
                    "CONFIG_PREEMPT_RT",
                    "CONFIG_PREEMPT_LAZY",
                    "CONFIG_PREEMPT",
                    "CONFIG_PREEMPT_VOLUNTARY",
                    "CONFIG_PREEMPT_NONE",
                )
                if cfg.get(k) == "y"
            ),
            None,
        ),
        "numa_balancing": root.read("/proc/sys/kernel/numa_balancing"),
    }
    if sched["sched_cache_enabled"] is None:
        unavailable["sched.sched_cache_enabled"] = "debugfs not readable or kernel without SCHED_CACHE"
    if sched["preempt"] is None:
        unavailable["sched.preempt"] = "debugfs not readable"

    cpu0 = "/sys/devices/system/cpu/cpu0/cpufreq"
    x3d = next(iter(root.glob("/sys/bus/platform/drivers/amd_x3d_vcache/*/amd_x3d_mode")), None)
    cpufreq = {
        "driver": root.read(cpu0 + "/scaling_driver"),
        "governor": root.read(cpu0 + "/scaling_governor"),
        "epp": root.read(cpu0 + "/energy_performance_preference"),
        "amd_pstate_status": root.read("/sys/devices/system/cpu/amd_pstate/status"),
        "boost": root.read("/sys/devices/system/cpu/cpufreq/boost"),
        "x3d_mode": root.read(root.rel(x3d)) if x3d else None,
    }
    memory_policy = {
        "thp_enabled": bracketed(root.read("/sys/kernel/mm/transparent_hugepage/enabled")),
        "thp_defrag": bracketed(root.read("/sys/kernel/mm/transparent_hugepage/defrag")),
        "swappiness": root.read("/proc/sys/vm/swappiness"),
        "overcommit_memory": root.read("/proc/sys/vm/overcommit_memory"),
        "zswap_enabled": root.read("/sys/module/zswap/parameters/enabled"),
        "mglru_enabled": root.read("/sys/kernel/mm/lru_gen/enabled"),
    }
    nv_proc = root.read("/proc/driver/nvidia/version")
    drivers = {
        "nvidia": root.read("/sys/module/nvidia/version"),
        "nvidia_proc": nv_proc.splitlines()[0] if nv_proc else None,
        "amdgpu": root.read("/sys/module/amdgpu/version"),
    }
    firmware = {
        "bios_vendor": root.read("/sys/class/dmi/id/bios_vendor"),
        "bios_version": root.read("/sys/class/dmi/id/bios_version"),
        "bios_date": root.read("/sys/class/dmi/id/bios_date"),
        "board": root.read("/sys/class/dmi/id/board_name"),
        "microcode": next(
            (
                ln.split(":", 1)[1].strip()
                for ln in (root.read("/proc/cpuinfo") or "").splitlines()
                if ln.startswith("microcode")
            ),
            None,
        ),
    }
    hc = hash_cache or HashCache()
    model_docs = []
    for m in models:
        p = Path(m)
        if p.is_file():
            model_docs.append({"path": str(p), "sha256": hc.sha256(p), "size_bytes": p.stat().st_size})
        else:
            unavailable[f"models.{p}"] = "file not found"

    doc: Doc = {
        "schema": "ailx.environment-fingerprint/v0",
        "hardware_profile_id": hardware_profile_id,
        "kernel": {
            "release": release,
            "version": root.read("/proc/sys/kernel/version"),
            "cmdline": root.read("/proc/cmdline"),
            "config_sha256": cfg_sha,
        },
        "os": _os_release(root),
        "firmware": firmware,
        "drivers": drivers,
        "security": {
            "secure_boot": secure_boot,
            "lockdown": lockdown,
            "mitigations": mitigations,
            "iommu_groups": len(iommu) if iommu else None,
        },
        "sched": sched,
        "cpufreq": cpufreq,
        "memory_policy": memory_policy,
        "services": {"tuning_daemons": _services(root, unavailable)},
        "accelerator_state": _nvml_state(unavailable) if (use_nvml and root.is_live()) else [],
        "runtime_bundles": list(runtime_bundles),
        "models": model_docs,
        "harness": {"version": __version__, "git_sha": _git_sha(), "python": platform.python_version()},
        "unavailable": unavailable,
    }
    if platform_state is not None:
        doc["platform"] = platform_state
    return seal(doc)
