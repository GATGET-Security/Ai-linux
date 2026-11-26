# SPDX-License-Identifier: MIT
"""Start/stop samplers, collect their streams, tag phases and summarise windows."""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ailx.core import validate

LINE_SCHEMA = "ailx.sample-stream/v0"


@dataclass
class Stream:
    name: str
    command: list[str]
    header: dict[str, Any] | None = None
    samples: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    proc: subprocess.Popen[str] | None = None
    thread: threading.Thread | None = None
    exited_early: bool = False

    @property
    def available(self) -> bool:
        return bool(self.header and self.header.get("available"))


def sampler_command(name: str, interval: float | None = None) -> list[str]:
    """Built-in samplers by name; ``exec:/path/to/bin arg ...`` for external (any-language) samplers."""
    if name.startswith("exec:"):
        cmd = name[5:].split()
    else:
        cmd = [sys.executable, "-m", f"ailx.telemetry.samplers.{name}"]
    if interval is not None:
        cmd += ["--interval", str(interval)]
    return cmd


class Supervisor:
    def __init__(self, samplers: list[str], interval: float | None = None) -> None:
        self.streams = [Stream(n, sampler_command(n, interval)) for n in samplers]
        self.phases: list[dict[str, Any]] = []
        self.clock_pair: dict[str, int] = {}
        self._stopping = False

    def _reader(self, st: Stream) -> None:
        assert st.proc is not None and st.proc.stdout is not None
        for line in st.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                validate(obj, LINE_SCHEMA)
            except Exception as exc:
                st.errors.append(f"invalid line: {exc}"[:300])
                continue
            kind = obj["kind"]
            if kind == "header":
                st.header = obj
            elif kind == "sample":
                st.samples.append(obj)
            else:
                st.events.append(obj)
        st.proc.wait()
        if not self._stopping and st.available:
            st.exited_early = True

    def start(self, settle_s: float = 0.3) -> None:
        self.clock_pair = {"realtime_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}
        for st in self.streams:
            st.proc = subprocess.Popen(st.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            st.thread = threading.Thread(target=self._reader, args=(st,), daemon=True)
            st.thread.start()
        time.sleep(settle_s)

    def mark(self, phase: str, edge: str) -> None:
        self.phases.append({"phase": phase, "edge": edge, "t_ns": time.monotonic_ns()})

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping = True
        for st in self.streams:
            if st.proc and st.proc.poll() is None:
                st.proc.terminate()
        for st in self.streams:
            if st.proc:
                try:
                    st.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    st.proc.kill()
            if st.thread:
                st.thread.join(timeout=timeout)

    def window(self, phase: str = "measure") -> tuple[int, int] | None:
        start = next((p["t_ns"] for p in self.phases if p["phase"] == phase and p["edge"] == "start"), None)
        end = next((p["t_ns"] for p in self.phases if p["phase"] == phase and p["edge"] == "end"), None)
        return (start, end) if start is not None and end is not None else None

    def failed(self) -> list[str]:
        return [st.name for st in self.streams if st.exited_early]

    def raw(self) -> dict[str, Any]:
        return {
            "clock_pair": self.clock_pair,
            "phases": self.phases,
            "streams": {
                st.name: {
                    "command": st.command,
                    "header": st.header,
                    "samples": st.samples,
                    "events": st.events,
                    "errors": st.errors,
                    "exited_early": st.exited_early,
                }
                for st in self.streams
            },
        }

    def summary(self, phase: str = "measure") -> dict[str, Any]:
        win = self.window(phase)
        out: dict[str, Any] = {}
        for st in self.streams:
            if not st.available:
                out[st.name] = {
                    "available": False,
                    "reason": (st.header or {}).get("reason", "no header (sampler failed to start)"),
                }
                continue
            assert st.header is not None
            out[st.name] = {
                "available": True,
                "simulated": bool(st.header.get("simulated")),
                "exited_early": st.exited_early,
                "metrics": summarize(st.header["metrics"], st.samples, win),
            }
        return out


def summarize(
    metrics: list[dict[str, Any]], samples: list[dict[str, Any]], window: tuple[int, int] | None
) -> dict[str, Any]:
    """Counters → delta inside the window (wrap-aware); gauges → mean/min/max/n."""
    if window:
        lo, hi = window
        # include the last sample before the window and the first after it as counter anchors
        before = [s for s in samples if s["t_ns"] <= lo][-1:]
        after = [s for s in samples if s["t_ns"] >= hi][:1]
        inside = [s for s in samples if lo < s["t_ns"] < hi]
        sel = before + inside + after
    else:
        inside = sel = samples
    res: dict[str, Any] = {}
    for m in metrics:
        name = m["name"]
        if m["type"] == "counter":
            pts = [(s["t_ns"], s["values"].get(name)) for s in sel if s["values"].get(name) is not None]
            if len(pts) < 2:
                res[name] = {"delta": None, "unit": m["unit"], "n": len(pts)}
                continue
            wrap = float(m.get("wrap") or 0)
            delta = 0.0
            for (_, a), (_, b) in itertools.pairwise(pts):
                d = b - a
                if d < 0 and wrap > 0:
                    d += wrap
                delta += d
            dur = (pts[-1][0] - pts[0][0]) / 1e9
            res[name] = {"delta": delta, "unit": m["unit"], "duration_s": dur, "n": len(pts)}
        else:
            vals = [s["values"].get(name) for s in inside if s["values"].get(name) is not None]
            if not vals:
                res[name] = {"mean": None, "unit": m["unit"], "n": 0}
                continue
            res[name] = {
                "mean": sum(vals) / len(vals),
                "min": min(vals),
                "max": max(vals),
                "unit": m["unit"],
                "n": len(vals),
            }
    return res


def energy_summary(tele: dict[str, Any]) -> dict[str, Any]:
    """Energy in joules per source; the wall meter is ground truth only if not simulated."""
    out: dict[str, Any] = {}
    nv = tele.get("nvml", {})
    if nv.get("available"):
        gpu = {k: v["delta"] / 1000.0 for k, v in nv["metrics"].items() if k.endswith("energy_mj") and v.get("delta")}
        if gpu:
            out["gpu_j"] = gpu
            out["gpu_total_j"] = sum(gpu.values())
    ra = tele.get("rapl", {})
    if ra.get("available"):
        cpu = {k: v["delta"] / 1e6 for k, v in ra["metrics"].items() if v.get("delta") is not None}
        if cpu:
            out["rapl_j"] = cpu
    for name, st in tele.items():
        if name.startswith("meter") and st.get("available"):
            e = st["metrics"].get("wall.energy_j", {})
            if e.get("delta") is not None:
                out["wall_j"] = e["delta"]
                out["wall_simulated"] = bool(st.get("simulated"))
    out["ground_truth"] = "wall" if ("wall_j" in out and not out.get("wall_simulated")) else "none"
    return out
