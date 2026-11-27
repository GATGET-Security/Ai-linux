# SPDX-License-Identifier: MIT
"""Execute one workload run with telemetry and produce a sealed result record (WP3)."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from ailx.adapters import RunContext, get_adapter
from ailx.core import Doc, seal, utcnow
from ailx.store import Store
from ailx.telemetry.supervisor import Supervisor, energy_summary
from ailx.workloads import workload_sha256

DEFAULT_SAMPLERS = ["psi", "cpu"]
NVIDIA_THROTTLE_HW_BITS = 0x8 | 0x20 | 0x40 | 0x80  # HW slowdown, SW thermal, HW thermal, HW power brake


class SetupError(RuntimeError):
    pass


def run_setup_commands(commands: list[list[str]], log: list[str]) -> None:
    """Operator-supplied per-arm hooks (e.g. a GPU power limit). AI Linux applies no knobs itself."""
    for cmd in commands:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log.append(f"$ {' '.join(cmd)} -> {r.returncode}\n{r.stdout}{r.stderr}")
        if r.returncode != 0:
            raise SetupError(f"setup command failed: {' '.join(cmd)} (exit {r.returncode})")


def _start_server(server: Doc, log: list[str]) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        server["command"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    deadline = time.monotonic() + float(server.get("startup_timeout_s", 600))
    url = server["health_url"]
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SetupError(f"server exited during startup (exit {proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    log.append(f"server healthy at {url}")
                    return proc
        except OSError:
            pass
        time.sleep(1.0)
    _stop_server(proc)
    raise SetupError(f"server not healthy within timeout at {url}")


def _stop_server(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


ENERGY_METRICS = {"gpu_energy_j": "gpu_total_j", "wall_energy_j": "wall_j"}


def _energy_metrics(workload: Doc, energy: Doc) -> dict[str, dict[str, Any]]:
    """Expose energy of the measure window as campaign metrics when the workload declares them.

    With a fixed amount of work per run (fixed token counts), energy per run is proportional
    to energy per token. Simulated meters never produce a metric.
    """
    wanted = {m["name"] for m in workload["metrics"]}
    out: dict[str, dict[str, Any]] = {}
    for metric, key in ENERGY_METRICS.items():
        if metric in wanted and isinstance(energy.get(key), int | float):
            if key == "wall_j" and energy.get("wall_simulated"):
                continue
            out[metric] = {"value": float(energy[key]), "unit": "J"}
    if "cpu_energy_j" in wanted and energy.get("rapl_j"):
        pkg = [v for k, v in energy["rapl_j"].items() if "package" in k]
        if pkg:
            out["cpu_energy_j"] = {"value": float(sum(pkg)), "unit": "J"}
    return out


def _stability_events(tele_raw: dict[str, Any]) -> list[Doc]:
    events: list[Doc] = []
    for name, st in tele_raw["streams"].items():
        events += [{"source": name, **e} for e in st["events"] if e.get("name") != "nvml-shutdown"]
        if st.get("exited_early"):
            events.append({"source": name, "name": "sampler-exited-early"})
        for s in st["samples"]:
            for key, val in s["values"].items():
                if key.endswith("throttle_reasons") and val and int(val) & NVIDIA_THROTTLE_HW_BITS:
                    events.append(
                        {
                            "source": name,
                            "name": "gpu-throttle",
                            "t_ns": s["t_ns"],
                            "attrs": {"metric": key, "reasons": int(val)},
                        }
                    )
                    break
    return events[:200]


def execute_run(
    workload: Doc,
    store: Store,
    hardware_profile_id: str,
    fingerprint_id: str,
    order_index: int = 0,
    seed: int = 0,
    campaign_id: str | None = None,
    arm_id: str | None = None,
    block: int | None = None,
    env: dict[str, str] | None = None,
    setup_commands: list[list[str]] | None = None,
) -> Doc:
    adapter = get_adapter(workload["adapter"])
    log: list[str] = []
    status, detail = "ok", None
    started = utcnow()
    t0 = time.monotonic()
    samplers = workload.get("telemetry") or DEFAULT_SAMPLERS
    sup = Supervisor(samplers)
    stdout = stderr = ""
    cmd: list[str] = []
    parsed = None
    with tempfile.TemporaryDirectory(prefix="ailx-run-") as tmp:
        ctx = RunContext(order_index=order_index, seed=seed, workdir=Path(tmp))
        server_proc = None
        try:
            if setup_commands:
                run_setup_commands(setup_commands, log)
            sup.start()
            server = workload["runtime"].get("server")
            if server:
                sup.mark("server-start", "start")
                server_proc = _start_server(server, log)
                sup.mark("server-start", "end")
            cmd = adapter.build(workload, ctx)
            sup.mark("measure", "start")
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=workload["timeout_s"], env={**os.environ, **(env or {})}
            )
            sup.mark("measure", "end")
            stdout, stderr = proc.stdout, proc.stderr
            if proc.returncode in (-9, 137):
                status, detail = "failed", "killed by SIGKILL (possibly the OOM killer; not verified)"
            elif proc.returncode != 0:
                status, detail = "failed", f"exit code {proc.returncode}"
            else:
                parsed = adapter.parse(stdout, stderr, workload, ctx)
                status, detail = parsed.status, parsed.detail
        except subprocess.TimeoutExpired as exc:
            sup.mark("measure", "end")
            status, detail = "timeout", f"exceeded {workload['timeout_s']} s"
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        except SetupError as exc:
            status, detail = "failed", str(exc)
        except (OSError, ValueError, KeyError) as exc:
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            if server_proc is not None:
                _stop_server(server_proc)
            sup.stop()
    tele = sup.summary("measure")
    raw = sup.raw()
    energy = energy_summary(tele)
    metrics = dict(parsed.metrics) if (parsed and status == "ok") else {}
    if metrics:
        metrics.update(_energy_metrics(workload, energy))
    if status == "ok" and sup.failed():
        status, detail = "telemetry-failed", f"sampler(s) died during the run: {', '.join(sup.failed())}"
    simulated_tele = [n for n, s in tele.items() if s.get("available") and s.get("simulated")]
    notes = (parsed.notes if parsed else []) + [f"telemetry source {n} is simulated" for n in simulated_tele]
    doc: Doc = {
        "schema": "ailx.result/v0",
        "campaign_id": campaign_id,
        "arm_id": arm_id,
        "block": block,
        "order_index": order_index,
        "workload_name": workload["name"],
        "workload_sha256": workload_sha256(workload),
        "fingerprint_id": fingerprint_id,
        "hardware_profile_id": hardware_profile_id,
        "measurement_kind": parsed.measurement_kind if parsed else "hardware",
        "started_at": started,
        "duration_s": time.monotonic() - t0,
        "status": status,
        "status_detail": detail,
        "metrics": metrics,
        "tool": {
            "name": parsed.tool_name if parsed else workload["adapter"],
            "version": parsed.tool_version if parsed else None,
            "command": cmd,
        },
        "telemetry": tele,
        "energy": energy,
        "stability_events": _stability_events(raw),
        "quality": None,
        "raw_ref": store.put_json_blob({"telemetry": raw, "notes": notes}),
        "logs_ref": store.put_blob(
            ("\n".join(log) + "\n--- stdout ---\n" + stdout + "\n--- stderr ---\n" + stderr[-200000:]).encode()
        ),
    }
    sealed = seal(doc)
    store.put(sealed)
    return sealed
