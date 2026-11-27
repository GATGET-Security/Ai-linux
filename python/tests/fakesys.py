# SPDX-License-Identifier: MIT
"""Constructed sysfs/procfs trees for probe tests (layout follows the kernel ABI; values are synthetic)."""

from __future__ import annotations

from pathlib import Path


def w(root: Path, rel: str, text: str) -> None:
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n")


def build_two_ccd_desktop(root: Path) -> Path:
    """16 cores / 32 threads, two L3 domains (96 MiB and 32 MiB) like a Ryzen 9 7950X3D, one NVIDIA dGPU."""
    cpuinfo = []
    for cpu in range(32):
        cpuinfo.append(
            f"processor\t: {cpu}\nvendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 9 7950X3D 16-Core Processor\n"
            "physical id\t: 0\nmicrocode\t: 0xa601209\n"
            "flags\t\t: fpu sse sse2 avx avx2 avx512f avx512_vnni avx512_bf16\n"
        )
    w(root, "/proc/cpuinfo", "\n".join(cpuinfo))
    w(root, "/proc/meminfo", "MemTotal:       65536000 kB\nMemFree:        60000000 kB")
    w(root, "/sys/devices/system/cpu/online", "0-31")
    w(root, "/sys/devices/system/cpu/smt/active", "1")
    for cpu in range(32):
        core = cpu % 16
        ccd0 = core < 8
        base = f"/sys/devices/system/cpu/cpu{cpu}/cache/index3"
        w(root, base + "/level", "3")
        w(root, base + "/shared_cpu_list", "0-7,16-23" if ccd0 else "8-15,24-31")
        w(root, base + "/size", "98304K" if ccd0 else "32768K")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index0/level", "1")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index0/type", "Data")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index0/size", "32K")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index2/level", "2")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index2/type", "Unified")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index2/size", "1024K")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id", str(core))
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/topology/physical_package_id", "0")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list", f"{core},{core + 16}")
        # synthetic values: the frequency CCD clocks higher than the V-Cache CCD
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq", "5250000" if ccd0 else "5758000")
    w(root, "/sys/devices/system/node/node0/cpulist", "0-31")
    w(
        root,
        "/sys/devices/system/node/node0/meminfo",
        "Node 0 MemTotal:       65536000 kB\nNode 0 MemFree:        60000000 kB",
    )
    dev = root / "sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0"
    for k, v in {
        "vendor": "0x10de",
        "device": "0x2684",
        "current_link_speed": "16.0 GT/s PCIe",
        "current_link_width": "16",
        "max_link_speed": "16.0 GT/s PCIe",
        "max_link_width": "16",
    }.items():
        w(dev, k, v)
    (root / "sys/bus/pci/drivers/nvidia").mkdir(parents=True, exist_ok=True)
    (dev / "driver").symlink_to(root / "sys/bus/pci/drivers/nvidia")
    (root / "sys/class/drm/card1").mkdir(parents=True, exist_ok=True)
    (root / "sys/class/drm/card1/device").symlink_to(dev)
    (root / "sys/class/drm/card1-DP-1").mkdir(parents=True, exist_ok=True)
    w(root, "/sys/block/nvme0n1/size", "3907029168")
    w(root, "/sys/block/nvme0n1/device/model", "Example PCIe5 NVMe")
    w(root, "/sys/block/nvme0n1/queue/rotational", "0")
    w(root, "/sys/class/nvme/nvme0/device/current_link_speed", "32.0 GT/s PCIe")
    w(root, "/sys/class/nvme/nvme0/device/current_link_width", "4")
    w(root, "/sys/class/powercap/intel-rapl:0/name", "package-0")
    w(root, "/sys/class/powercap/intel-rapl:0/energy_uj", "123456789")
    w(root, "/sys/class/powercap/intel-rapl:0/max_energy_range_uj", "262143328850")
    w(root, "/proc/driver/nvidia/version", "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.00")
    # fingerprint-relevant files
    w(root, "/proc/sys/kernel/osrelease", "7.0.0-15-generic")
    w(root, "/proc/sys/kernel/version", "#15-Ubuntu SMP PREEMPT_DYNAMIC")
    w(root, "/proc/cmdline", "BOOT_IMAGE=/vmlinuz-7.0.0-15-generic root=UUID=x ro quiet splash")
    w(root, "/etc/os-release", 'ID=ubuntu\nVERSION_ID="26.04"\nPRETTY_NAME="Ubuntu 26.04 LTS"')
    w(root, "/sys/kernel/security/lockdown", "none [integrity] confidentiality")
    w(root, "/sys/devices/system/cpu/vulnerabilities/spectre_v2", "Mitigation: Enhanced / Automatic IBRS")
    w(root, "/sys/kernel/mm/transparent_hugepage/enabled", "always [madvise] never")
    w(root, "/sys/kernel/mm/transparent_hugepage/defrag", "always defer defer+madvise [madvise] never")
    w(root, "/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver", "amd-pstate-epp")
    w(root, "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "powersave")
    w(root, "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference", "balance_performance")
    w(root, "/sys/bus/platform/drivers/amd_x3d_vcache/AMDI0101:00/amd_x3d_mode", "frequency")
    w(root, "/sys/kernel/sched_ext/state", "disabled")
    w(root, "/sys/module/nvidia/version", "580.00")
    w(
        root,
        "/boot/config-7.0.0-15-generic",
        "CONFIG_PREEMPT_LAZY=y\nCONFIG_SCHED_CLASS_EXT=y\n# CONFIG_SCHED_CACHE is not set",
    )
    return root


def build_single_llc_vm(root: Path) -> Path:
    """4 vCPUs, one L3, no GPU, no lockdown file (typical cloud VM)."""
    w(
        root,
        "/proc/cpuinfo",
        "\n".join(
            f"processor\t: {i}\nvendor_id\t: GenuineIntel\nmodel name\t: Example vCPU\nflags\t\t: avx2\n"
            for i in range(4)
        ),
    )
    w(root, "/proc/meminfo", "MemTotal:       16000000 kB")
    w(root, "/sys/devices/system/cpu/online", "0-3")
    for cpu in range(4):
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/level", "3")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/shared_cpu_list", "0-3")
        w(root, f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/size", "16384K")
    w(root, "/proc/sys/kernel/osrelease", "6.18.0-test")
    w(root, "/etc/os-release", 'ID=debian\nVERSION_ID="13"')
    return root
