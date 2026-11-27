# SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path

from ailx.probe import collect_fingerprint, collect_hardware_profile
from ailx.probe.fingerprint import HashCache
from ailx.probe.sysfs import bracketed, parse_cpu_list, parse_size


def test_parsers() -> None:
    assert parse_cpu_list("0-3,8,10-11") == [0, 1, 2, 3, 8, 10, 11]
    assert parse_size("98304K") == 96 * 1024**2
    assert bracketed("always [madvise] never") == "madvise"


def test_two_ccd_topology(desktop_root: Path) -> None:
    hwp = collect_hardware_profile(desktop_root, use_nvml=False)
    cpu = hwp["cpu"]
    assert cpu["logical_cpus"] == 32 and cpu["smt_active"] is True
    sizes = sorted(d["size_bytes"] for d in cpu["llc_domains"])
    assert sizes == [32 * 1024**2, 96 * 1024**2]  # asymmetric X3D-like LLCs
    assert all(len(d["cpus"]) == 16 for d in cpu["llc_domains"])
    assert "avx512_vnni" in cpu["isa_flags"]
    gpu = hwp["accelerators"][0]
    assert gpu["vendor"] == "nvidia" and gpu["pcie"]["current_width"] == 16
    # no NVML in the fixture: memory is unknown and the gap is recorded, not guessed
    assert gpu["memory_domains"][0]["kind"] == "unknown"
    assert any("memory" in k for k in hwp["unavailable"])
    assert hwp["storage"][0]["link"]["current_width"] == 4


def test_single_llc_vm(vm_root: Path) -> None:
    hwp = collect_hardware_profile(vm_root, use_nvml=False)
    assert len(hwp["cpu"]["llc_domains"]) == 1
    assert hwp["accelerators"] == []


def test_ids_are_stable(desktop_root: Path, tmp_path: Path) -> None:
    a = collect_hardware_profile(desktop_root, use_nvml=False)
    b = collect_hardware_profile(desktop_root, use_nvml=False)
    assert a["id"] == b["id"] and a["collected_at"] != "" and a["id"].startswith("hwp-")
    hc = HashCache(tmp_path / "h.json")
    f1 = collect_fingerprint(a["id"], desktop_root, use_nvml=False, hash_cache=hc)
    f2 = collect_fingerprint(a["id"], desktop_root, use_nvml=False, hash_cache=hc)
    assert f1["id"] == f2["id"]


def test_fingerprint_fields(desktop_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 100)
    fp = collect_fingerprint(
        "hwp-" + "0" * 64,
        desktop_root,
        models=[model, tmp_path / "missing.gguf"],
        use_nvml=False,
        hash_cache=HashCache(tmp_path / "h.json"),
    )
    assert fp["security"]["lockdown"] == "integrity"
    assert fp["sched"]["sched_ext_available"] is True and fp["sched"]["preempt_config"] == "CONFIG_PREEMPT_LAZY"
    assert fp["sched"]["sched_cache_config"] is None  # 7.0 kernel: no SCHED_CACHE
    assert fp["cpufreq"]["x3d_mode"] == "frequency" and fp["memory_policy"]["thp_enabled"] == "madvise"
    assert fp["os"]["version_id"] == "26.04"
    assert fp["models"][0]["size_bytes"] == 104
    assert any("missing.gguf" in k for k in fp["unavailable"])
    assert "security.secure_boot" in fp["unavailable"]  # not readable -> recorded


def test_fingerprint_changes_with_config(desktop_root: Path, tmp_path: Path) -> None:
    hc = HashCache(tmp_path / "h.json")
    a = collect_fingerprint("hwp-" + "0" * 64, desktop_root, use_nvml=False, hash_cache=hc)
    (desktop_root / "sys/kernel/mm/transparent_hugepage/enabled").write_text("[always] madvise never\n")
    b = collect_fingerprint("hwp-" + "0" * 64, desktop_root, use_nvml=False, hash_cache=hc)
    assert a["id"] != b["id"]
