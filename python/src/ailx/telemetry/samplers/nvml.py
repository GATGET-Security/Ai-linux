# SPDX-License-Identifier: MIT
"""NVIDIA GPU sampler via NVML (optional dependency ``nvidia-ml-py``).

Prefers the total-energy counter over sampled power, because sampled power can miss
short peaks [S-29]. Reports instant power, clocks, temperature, memory and throttle reasons.
"""

from __future__ import annotations

import sys
from typing import Any

from ailx.telemetry.protocol import emit, header, now_ns, parse_interval, run_loop


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    interval = parse_interval(argv, 0.1)
    try:
        import pynvml
    except ImportError:
        header("nvml", "0.1.0", [], interval, available=False, reason="nvidia-ml-py not installed")
        return
    try:
        pynvml.nvmlInit()
        handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
    except Exception as exc:
        header("nvml", "0.1.0", [], interval, available=False, reason=f"NVML unavailable: {type(exc).__name__}")
        return
    if not handles:
        header("nvml", "0.1.0", [], interval, available=False, reason="no NVIDIA GPU")
        return

    def opt(fn: Any, *a: Any) -> float | None:
        try:
            return float(fn(*a))
        except Exception:
            return None

    def read() -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for i, h in enumerate(handles):
            p = f"gpu{i}."
            out[p + "energy_mj"] = opt(pynvml.nvmlDeviceGetTotalEnergyConsumption, h)
            out[p + "power_mw"] = opt(pynvml.nvmlDeviceGetPowerUsage, h)
            out[p + "sm_clock_mhz"] = opt(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_SM)
            out[p + "mem_clock_mhz"] = opt(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_MEM)
            out[p + "temp_c"] = opt(pynvml.nvmlDeviceGetTemperature, h, pynvml.NVML_TEMPERATURE_GPU)
            try:
                out[p + "mem_used_bytes"] = float(pynvml.nvmlDeviceGetMemoryInfo(h).used)
            except Exception:
                out[p + "mem_used_bytes"] = None
            out[p + "throttle_reasons"] = opt(pynvml.nvmlDeviceGetCurrentClocksEventReasons, h)
        return out

    first = read()
    units = {
        "energy_mj": ("mJ", "counter"),
        "power_mw": ("mW", "gauge"),
        "sm_clock_mhz": ("MHz", "gauge"),
        "mem_clock_mhz": ("MHz", "gauge"),
        "temp_c": ("C", "gauge"),
        "mem_used_bytes": ("B", "gauge"),
        "throttle_reasons": ("bitmask", "gauge"),
    }
    metrics = [{"name": k, "unit": units[k.split(".", 1)[1]][0], "type": units[k.split(".", 1)[1]][1]} for k in first]
    header("nvml", "0.1.0", metrics, interval)
    try:
        run_loop(read, interval)
    finally:
        emit({"kind": "event", "t_ns": now_ns(), "name": "nvml-shutdown"})
        pynvml.nvmlShutdown()


if __name__ == "__main__":
    main()
