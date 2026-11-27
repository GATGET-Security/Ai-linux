# SPDX-License-Identifier: MIT
"""Hardware capability engine against constructed sysfs trees (values synthetic, layout per kernel ABI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ailx.compute import providers
from ailx.compute.capabilities import collect_capabilities, isa, memory_budget
from ailx.system.paths import Layout


def layout(root: Path) -> Layout:
    return Layout(
        root=root,
        state_dir=root / "var/lib/ailx",
        engines_dir=root / "var/lib/ailx/engines",
        helper="/nonexistent",
        mode="user",
        models_dir=Path("/var/lib/ailx/models"),
    )


def add_engine(lay: Layout, variant: str, install_id: str) -> None:
    d = lay.engines_dir / "llama.cpp" / install_id
    (d / "bin").mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps({"engine": "llama.cpp", "install_id": install_id, "variant": variant, "kind": "cmake"})
    )
    cur = lay.engines_dir / "llama.cpp" / "current"
    if not cur.is_symlink():
        cur.symlink_to(install_id)


def test_two_ccd_desktop_topology(desktop_root: Path) -> None:
    caps = collect_capabilities(layout(desktop_root), use_nvml=False)
    cpu = caps["cpu"]
    assert cpu["physical_cores"] == 16 and cpu["logical_cpus"] == 32 and cpu["topology_source"] == "sysfs core_id"
    big, small = cpu["llc_domains"]
    assert big["physical_cores"] == small["physical_cores"] == 8
    assert big["primary_cpus"] == list(range(8)) and small["primary_cpus"] == list(range(8, 16))
    assert (big["role"], small["role"]) == ("large-cache", "frequency") and cpu["asymmetric_llc"]
    assert big["l3_bytes"] == 96 * 2**20 and small["max_khz"] > big["max_khz"]
    assert cpu["caches_per_core_bytes"] == {"l1d": 32 * 1024, "l2": 1024 * 1024}
    assert cpu["x3d_mode"] == "frequency" and "avx512_bf16" in cpu["isa"]["ai_extensions"]
    assert caps["numa"][0]["memory_total_bytes"] == 65536000 * 1024
    # the GPU exists in sysfs, but without NVML/nvidia-smi its memory is unknown and that is recorded
    gpu = caps["accelerators"][0]
    assert gpu["id"] == "nvidia:0" and gpu["memory"]["total_bytes"] is None
    assert "nvidia:0.memory" in caps["unavailable"] and "nvidia:0.cuda" in caps["unavailable"]


def test_id_ignores_live_values(desktop_root: Path) -> None:
    a = collect_capabilities(layout(desktop_root), use_nvml=False)
    (desktop_root / "proc/loadavg").write_text("9.99 9.99 9.99 1/1 1\n")
    b = collect_capabilities(layout(desktop_root), use_nvml=False)
    assert a["id"] == b["id"] and a["live"] != b["live"]


def test_backend_needs_driver_stack_and_engine_build(desktop_root: Path) -> None:
    lay = layout(desktop_root)
    caps = collect_capabilities(lay, use_nvml=False)
    assert not any(b["usable"] for b in caps["backends"])  # no engine installed: nothing is usable
    (desktop_root / "usr/lib/x86_64-linux-gnu").mkdir(parents=True)
    (desktop_root / "usr/lib/x86_64-linux-gnu/libcuda.so.1").write_text("")
    add_engine(lay, "cuda", "98cc730cc8f8-cuda")
    add_engine(lay, "cpu", "98cc730cc8f8-cpu")
    caps = collect_capabilities(lay, use_nvml=False)
    by = {b["backend"]: b for b in caps["backends"]}
    assert by["cuda"]["usable"] and by["cuda"]["devices"] == ["nvidia:0"]
    assert by["cpu"]["usable"] and not by["vulkan"]["usable"]
    assert {e["install_id"]: e["current"] for e in caps["engines"]} == {
        "98cc730cc8f8-cpu": False,
        "98cc730cc8f8-cuda": True,
    }


def test_vm_without_topology(vm_root: Path) -> None:
    caps = collect_capabilities(layout(vm_root), use_nvml=False)
    assert caps["cpu"]["topology_source"].startswith("assumed")
    assert caps["cpu"]["isa"]["level"] is None and caps["accelerators"] == []


def test_isa_levels() -> None:
    v3 = [
        "sse",
        "sse2",
        "cx16",
        "lahf_lm",
        "popcnt",
        "sse4_1",
        "sse4_2",
        "ssse3",
        "avx",
        "avx2",
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "abm",
        "movbe",
        "xsave",
    ]
    assert isa(v3)["level"] == "x86-64-v3"
    assert isa([*v3, "avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"])["level"] == "x86-64-v4"
    assert isa(["asimd", "asimddp", "i8mm"])["ai_extensions"] == ["asimd", "asimddp", "i8mm"]


def test_nvidia_smi_provider(fake_gpu: Path) -> None:
    live: dict[str, object] = {}
    unavailable: dict[str, str] = {}
    devs = providers._nvidia_smi(live, unavailable)
    assert len(devs) == 1 and not unavailable
    d = devs[0]
    assert d["name"] == "NVIDIA GeForce RTX 4090" and d["compute_capability"] == "8.9"
    assert d["memory"]["total_bytes"] == 24564 * 2**20
    assert d["memory"]["bandwidth"] == {"bytes_per_s": 1008e9, "provenance": "datasheet:H-12", "kind": "theoretical"}
    assert live["nvidia:0"] == {"vram_free_bytes": 24164 * 2**20, "vram_used_bytes": 400 * 2**20}


def test_theoretical_bandwidth_formula() -> None:
    # RTX 4090 inputs (max memory clock 10501 MHz, 384-bit) reproduce the datasheet figure within 0.1 %
    assert providers.theoretical_bw(10501, 384) == pytest.approx(1008e9, rel=1e-3)
    assert providers.theoretical_bw(None, 384) is None


def test_memory_budget(desktop_root: Path) -> None:
    caps = collect_capabilities(layout(desktop_root), use_nvml=False)
    caps["accelerators"][0]["memory"].update(kind="discrete", total_bytes=24 * 2**30)
    b = memory_budget(caps, reserve_ram_bytes=4 * 2**30, reserve_vram_bytes=2**30)
    assert b["ram_usable_bytes"] == 65536000 * 1024 - 4 * 2**30
    assert b["gpus"][0]["usable_bytes"] == 23 * 2**30
