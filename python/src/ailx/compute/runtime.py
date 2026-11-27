# SPDX-License-Identifier: MIT
"""Managed inference processes: start, status, stop, with admission control and CPU placement.

* **Admission control** — before a start, the planner predicts the instance's host and device memory
  (from the model file and the request). The sum of the predictions of all running instances plus the
  new one must stay within the budget (total minus reserve), and the new one must fit memory that is
  free *now*. Otherwise the start is refused with the reason and alternatives, or — only with
  ``preempt=True`` — lower-priority instances are stopped first. Predictions are uncalibrated
  (H-R-11), so the budget keeps a margin; llama.cpp's own ``--fit`` still fits layers at load time.
* **Priorities** — classes ``interactive`` / ``batch`` / ``background`` map to nice value,
  ``oom_score_adj`` (only ever raised, which needs no privilege) and admission order.
* **Placement** — ``none`` (default: kernel scheduling, i.e. stock behaviour), ``llc:<id>``,
  ``numa:<node>`` or ``large-cache``/``frequency`` (CCD roles). Explicit placements are
  **experimental** (H-H-01, H-K-09, H-K-11: not hardware-validated) and are applied with
  ``sched_setaffinity`` on the process only — no system setting changes.
* **Trust** — instance records live in the (group-writable) state directory. Before signalling a
  process, its start time and executable are re-checked against /proc, and the executable must lie
  inside the engines directory, so an edited record cannot redirect ``stop`` to another process.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import signal
import socket
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ailx.compute.model import analyze
from ailx.compute.planner import OVERHEAD, Request, plan
from ailx.core import Doc, utcnow, validate
from ailx.system.engines import EngineError, EngineManager
from ailx.system.paths import SYSTEM_CONFIG, Layout

MiB = 1024**2
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PRIORITIES = {  # class -> (nice, oom_score_adj, rank); higher rank wins admission
    "interactive": (0, 0, 3),
    "batch": (5, 300, 2),
    "background": (10, 600, 1),
}
PLACEMENTS = ("none", "large-cache", "frequency")
DEFAULTS = {"reserve_ram_mib": 2048, "reserve_vram_mib": 0, "host": "127.0.0.1"}


class RuntimeError_(RuntimeError):
    pass


class AdmissionError(RuntimeError_):
    def __init__(self, msg: str, decision: Doc) -> None:
        super().__init__(msg)
        self.decision = decision


def runtime_config(layout: Layout) -> dict[str, Any]:
    cfg: dict[str, Any] = dict(DEFAULTS)
    p = layout.host(SYSTEM_CONFIG)
    if p.is_file():
        with open(p, "rb") as f:
            cfg.update(tomllib.load(f).get("runtime", {}))
    for key in ("reserve_ram_mib", "reserve_vram_mib"):
        env = os.environ.get("AILX_" + key.upper())
        if env:
            cfg[key] = int(env)
    return cfg


@dataclass
class StartSpec:
    name: str
    model: str
    ctx: int | None = None
    parallel: int = 1
    priority: str = "interactive"
    placement: str = "none"
    config: str = "engine-default"  # engine-default | planned | tuned
    tuned: Doc | None = None  # engine_config from an adopted tuning record
    host: str | None = None
    port: int | None = None
    threads: int | None = None
    device: str = "auto"
    allow_lossy: bool = False
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"
    extra_args: list[str] = field(default_factory=list)


def _proc_start_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(stat[stat.rfind(")") + 2 :].split()[19])  # field 22: starttime


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def placement_cpus(caps: Doc, policy: str) -> list[int] | None:
    """CPU set for a placement policy; ``None`` means no pinning (stock scheduling)."""
    if policy == "none":
        return None
    doms = caps["cpu"]["llc_domains"]
    if policy in ("large-cache", "frequency"):
        dom = next((d for d in doms if d["role"] == policy), None)
        if dom is None:
            raise RuntimeError_(f"placement {policy!r}: this CPU has no asymmetric LLC domains")
        return list(dom["cpus"])
    kind, _, val = policy.partition(":")
    if kind == "mask" and val.startswith("0x"):  # adopted experimental placement from a tuning record
        bits = int(val, 16)
        cpus = [c for c in range(bits.bit_length()) if bits >> c & 1]
        if not cpus:
            raise RuntimeError_("empty CPU mask")
        return cpus
    if kind == "llc" and val.isdigit():
        dom = next((d for d in doms if d["id"] == int(val)), None)
        if dom is None:
            raise RuntimeError_(f"no LLC domain {val}")
        return list(dom["cpus"])
    if kind == "numa" and val.isdigit():
        node = next((n for n in caps["numa"] if n["node"] == int(val)), None)
        if node is None:
            raise RuntimeError_(f"no NUMA node {val}")
        return list(node["cpus"])
    raise RuntimeError_(
        f"unknown placement {policy!r} (none, large-cache, frequency, llc:<id>, numa:<node>, mask:0x..)"
    )


SERVER_FLAGS = [
    ("n_gpu_layers", "-ngl"),
    ("n_cpu_moe", "-ncmoe"),
    ("batch_size", "-b"),
    ("ubatch_size", "-ub"),
    ("flash_attn", "-fa"),
    ("cache_type_k", "-ctk"),
    ("cache_type_v", "-ctv"),
    ("threads", "-t"),
]


def validate_settings(explicit: Doc, allow_lossy: bool) -> Doc:
    """Settings from tuning records are untrusted input (group-writable state): enforce types and domains.

    Lossy KV-cache types need an explicit ``allow_lossy`` at every start (ADR-0004: no silent quality
    reduction), even if a record claims a passed quality gate.
    """
    from ailx.compute.model import KV_TYPE_BYTES, LOSSLESS_KV

    known = {k for k, _ in SERVER_FLAGS}
    out: Doc = {}
    for k, v in explicit.items():
        if k not in known:
            raise RuntimeError_(f"unknown engine setting {k!r}")
        if k == "flash_attn":
            if v not in ("on", "off", "auto"):
                raise RuntimeError_(f"flash_attn must be on/off/auto, not {v!r}")
        elif k in ("cache_type_k", "cache_type_v"):
            if v not in KV_TYPE_BYTES:
                raise RuntimeError_(f"unknown KV cache type {v!r}")
            if v not in LOSSLESS_KV and not allow_lossy:
                raise RuntimeError_(f"{k}={v} is LOSSY: pass --allow-lossy to use it")
        elif not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1_000_000):
            raise RuntimeError_(f"{k} must be a non-negative integer, not {v!r}")
        out[k] = v
    return out


def server_args(cfg: Doc, explicit: Doc) -> list[str]:
    """llama-server flags: context and slots always; KV types if not f16; then only ``explicit`` settings.

    ``engine-default`` passes nothing explicit (the engine fits layers itself), ``tuned`` passes exactly
    the adopted overrides — the same delta that was measured against the engine defaults — and
    ``planned`` passes the planner's full configuration.
    """
    args = ["-c", str(cfg["ctx_size"]), "-np", str(cfg.get("parallel", 1))]
    merged = {**{k: cfg.get(k) for k in ("cache_type_k", "cache_type_v") if cfg.get(k, "f16") != "f16"}, **explicit}
    for key, flag in SERVER_FLAGS:
        if merged.get(key) is not None:
            val = merged[key]
            if key == "flash_attn" and isinstance(val, int | bool):
                val = "on" if val else "off"
            args += [flag, str(val)]
    return args


class RuntimeManager:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.dir = layout.state_dir / "runtime"
        self.cfg = runtime_config(layout)

    # ---------------------------------------------------------------- records
    def _rec(self, name: str) -> Path:
        if not NAME.match(name):
            raise RuntimeError_(f"invalid instance name {name!r} (a-z, 0-9, '-', max 32)")
        return self.dir / "instances" / f"{name}.json"

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _save(self, rec: Doc) -> None:
        validate(rec, "ailx.runtime-instance/v0")
        p = self._rec(rec["name"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, p)

    def load(self, name: str) -> Doc:
        p = self._rec(name)
        if not p.is_file():
            raise RuntimeError_(f"no instance {name!r}")
        rec: Doc = json.loads(p.read_text())
        validate(rec, "ailx.runtime-instance/v0")
        return rec

    def instances(self) -> list[Doc]:
        out = []
        for p in sorted((self.dir / "instances").glob("*.json")):
            try:
                out.append(self.refresh(json.loads(p.read_text())))
            except (ValueError, OSError):
                continue
        return out

    # ---------------------------------------------------------------- process identity
    def _verified_pid(self, rec: Doc) -> int | None:
        """PID of the recorded process if it is still that process (start time + executable)."""
        pid = rec.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            return None
        if _proc_start_ticks(pid) != rec.get("start_ticks"):
            return None
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            return None
        binary = os.path.realpath(rec["engine"]["binary"])
        engines = os.path.realpath(self.layout.engines_dir)
        if not binary.startswith(engines + os.sep):
            return None
        # argv[0] for binaries; argv[1] when the engine entry point is an interpreter script
        if not any(a and os.path.realpath(a.decode(errors="replace")) == binary for a in argv[:2]):
            return None
        return pid

    def refresh(self, rec: Doc) -> Doc:
        if rec["status"] == "running" and self._verified_pid(rec) is None:
            rec["status"] = "exited"
            rec["stopped_at"] = rec.get("stopped_at") or utcnow()
        return rec

    # ---------------------------------------------------------------- admission
    def admission(self, caps: Doc, new_plan: Doc, priority: str) -> Doc:
        reserve_ram = int(self.cfg["reserve_ram_mib"]) * MiB
        reserve_vram = int(self.cfg["reserve_vram_mib"]) * MiB
        running = [r for r in self.instances() if r["status"] == "running"]
        ram_total = (caps["memory"]["total_bytes"] or 0) - reserve_ram
        used_ram = sum(r["plan"]["host_bytes"] for r in running)
        dev = new_plan["device"]
        dev_total = None
        for d in caps["accelerators"]:
            if d["id"] == dev and d["memory"]["total_bytes"]:
                dev_total = d["memory"]["total_bytes"] - reserve_vram
        used_dev = sum(
            r["plan"]["device_bytes"] + OVERHEAD["fit_margin_bytes"] for r in running if r["plan"]["device"] == dev
        )
        need_ram = new_plan["memory"]["host_bytes"]
        need_dev = new_plan["memory"]["device_bytes"] + OVERHEAD["fit_margin_bytes"] if dev != "cpu" else 0
        reasons = []
        if not new_plan["feasible"]:
            reasons.append("the model does not fit this machine with the requested settings")
        if used_ram + need_ram > ram_total:
            reasons.append(
                f"RAM budget: running instances reserve {used_ram // MiB} MiB, this one needs {need_ram // MiB} MiB, "
                f"budget {ram_total // MiB} MiB (total minus reserve)"
            )
        if dev_total is not None and used_dev + need_dev > dev_total:
            reasons.append(
                f"{dev} budget: running instances reserve {used_dev // MiB} MiB, this one needs {need_dev // MiB} MiB, "
                f"budget {dev_total // MiB} MiB"
            )
        rank = PRIORITIES[priority][2]
        preemptible = sorted(
            (r for r in running if PRIORITIES[r["priority"]][2] < rank),
            key=lambda r: (PRIORITIES[r["priority"]][2], -r["plan"]["host_bytes"] - r["plan"]["device_bytes"]),
        )
        return {
            "admitted": not reasons,
            "reasons": reasons,
            "budget": {
                "ram_bytes": ram_total,
                "ram_reserved_by_running": used_ram,
                "device": dev,
                "device_bytes": dev_total,
                "device_reserved_by_running": used_dev,
            },
            "need": {"ram_bytes": need_ram, "device_bytes": need_dev},
            "preemptible": [r["name"] for r in preemptible],
            "estimate": new_plan["estimate"],
        }

    # ---------------------------------------------------------------- start / stop
    def prepare(self, spec: StartSpec, caps: Doc) -> Doc:
        if spec.priority not in PRIORITIES:
            raise RuntimeError_(f"unknown priority {spec.priority!r} ({', '.join(PRIORITIES)})")
        if spec.config not in ("engine-default", "planned", "tuned"):
            raise RuntimeError_(f"unknown config source {spec.config!r}")
        model = resolve_model(self.layout, spec.model)
        analysis = analyze(model)
        if analysis["format"] != "gguf":
            raise RuntimeError_("the managed runtime currently serves GGUF models with llama.cpp only")
        req = Request(
            ctx=spec.ctx,
            parallel=spec.parallel,
            device=spec.device,
            allow_lossy=spec.allow_lossy,
            cache_type_k=spec.cache_type_k,
            cache_type_v=spec.cache_type_v,
            reserve_ram_bytes=int(self.cfg["reserve_ram_mib"]) * MiB,
            reserve_vram_bytes=int(self.cfg["reserve_vram_mib"]) * MiB,
        )
        p = plan(caps, analysis, req)
        cfg = dict(p["engine_config"])
        explicit: Doc = {}
        if spec.config == "planned":
            explicit = {k: cfg[k] for k, _ in SERVER_FLAGS if cfg.get(k) is not None}
        elif spec.config == "tuned":
            if not spec.tuned:
                raise RuntimeError_("config 'tuned' needs an adopted tuning record (ailx tune show)")
            explicit = validate_settings(dict(spec.tuned), spec.allow_lossy)
        if spec.threads:
            explicit["threads"] = spec.threads
        cfg.update(explicit)
        try:
            binary, manifest = EngineManager(self.layout).binary("llama.cpp", "llama-server")
        except EngineError as exc:
            raise RuntimeError_(f"{exc} (with the llama-server target)") from exc
        cpus = placement_cpus(caps, spec.placement)
        host = spec.host or str(self.cfg["host"])
        port = spec.port or _free_port()
        cmd = [str(binary), "-m", str(model), "--host", host, "--port", str(port)]
        cmd += server_args(cfg, explicit)
        cmd += spec.extra_args
        nice, oom, _ = PRIORITIES[spec.priority]
        return {
            "name": spec.name,
            "cmd": cmd,
            "host": host,
            "port": port,
            "model": {"path": str(model), "id": analysis["id"], "name": analysis["name"]},
            "engine": {
                "engine": "llama.cpp",
                "install_id": manifest["install_id"],
                "binary": str(binary),
                "variant": manifest.get("variant"),
            },
            "config_source": spec.config,
            "engine_config": cfg,
            "explicit_settings": explicit,
            "priority": spec.priority,
            "placement": {"policy": spec.placement, "cpus": cpus, "experimental": spec.placement != "none"},
            "process": {"nice": nice, "oom_score_adj": oom},
            "plan": {
                "mode": p["mode"],
                "device": p["device"],
                "backend": p["backend"],
                "host_bytes": p["memory"]["host_bytes"],
                "device_bytes": p["memory"]["device_bytes"] if p["device"] != "cpu" else 0,
                "estimate": p["estimate"],
            },
            "_plan": p,
        }

    def start(self, spec: StartSpec, caps: Doc, preempt: bool = False, dry_run: bool = False) -> Doc:
        with self._lock():
            p = self._rec(spec.name)
            if p.is_file() and self.refresh(json.loads(p.read_text()))["status"] == "running":
                raise RuntimeError_(f"instance {spec.name!r} is already running")
            prep = self.prepare(spec, caps)
            full_plan = prep.pop("_plan")
            decision = self.admission(caps, full_plan, spec.priority)
            stopped: list[str] = []
            if not decision["admitted"] and preempt and full_plan["feasible"]:
                for victim in decision["preemptible"]:
                    if not dry_run:
                        self.stop(victim, reason=f"preempted by {spec.name} ({spec.priority})", locked=True)
                    stopped.append(victim)
                    still = [r for r in self.instances() if r["status"] == "running" and r["name"] not in stopped]
                    decision = self._recheck(caps, full_plan, spec.priority, still)
                    if decision["admitted"]:
                        break
            decision["preempted"] = stopped
            prep["admission"] = decision
            if not decision["admitted"]:
                raise AdmissionError("admission refused: " + "; ".join(decision["reasons"]), decision)
            if dry_run:
                prep["status"] = "dry-run"
                return prep
            log = self.dir / "logs" / f"{spec.name}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            cpus, nice, oom = prep["placement"]["cpus"], prep["process"]["nice"], prep["process"]["oom_score_adj"]

            def child_setup() -> None:  # runs in the child before exec; only per-process settings
                if cpus:
                    os.sched_setaffinity(0, cpus)
                if nice:
                    os.nice(nice)
                if oom:
                    with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as f:
                        f.write(str(oom))

            with open(log, "ab") as lf:
                proc = subprocess.Popen(
                    prep["cmd"],
                    stdin=subprocess.DEVNULL,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    preexec_fn=child_setup,  # no threads are started before this point
                )
            ticks = None
            for _ in range(50):
                ticks = _proc_start_ticks(proc.pid)
                if ticks is not None:
                    break
                time.sleep(0.01)
            rec: Doc = {
                "schema": "ailx.runtime-instance/v0",
                **prep,
                "pid": proc.pid,
                "start_ticks": ticks,
                "uid": os.getuid(),
                "log": str(log),
                "status": "running",
                "started_at": utcnow(),
                "stopped_at": None,
                "stop_reason": None,
            }
            self._save(rec)
            return rec

    def _recheck(self, caps: Doc, full_plan: Doc, priority: str, running: list[Doc]) -> Doc:
        saved = self.instances
        self.instances = lambda: running  # type: ignore[method-assign]
        try:
            return self.admission(caps, full_plan, priority)
        finally:
            self.instances = saved  # type: ignore[method-assign]

    def stop(self, name: str, timeout: float = 15.0, reason: str = "operator", locked: bool = False) -> Doc:
        ctx = contextlib.nullcontext() if locked else self._lock()
        with ctx:
            rec = self.refresh(self.load(name))
            pid = self._verified_pid(rec) if rec["status"] == "running" else None
            if pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGTERM)  # controlled shutdown: the server finishes and exits
                deadline = time.time() + timeout
                while time.time() < deadline and _proc_start_ticks(pid) == rec["start_ticks"]:
                    with contextlib.suppress(ChildProcessError):
                        os.waitpid(pid, os.WNOHANG)
                    time.sleep(0.1)
                if _proc_start_ticks(pid) == rec["start_ticks"]:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(pid, signal.SIGKILL)
                    reason += " (SIGKILL after timeout)"
            rec["status"] = "stopped"
            rec["stopped_at"] = utcnow()
            rec["stop_reason"] = reason
            self._save(rec)
            return rec

    def remove(self, name: str) -> None:
        rec = self.refresh(self.load(name))
        if rec["status"] == "running":
            raise RuntimeError_(f"{name} is running; stop it first")
        self._rec(name).unlink()

    # ---------------------------------------------------------------- observation
    def status(self, name: str) -> Doc:
        rec = self.refresh(self.load(name))
        live: Doc = {"health": None}
        pid = self._verified_pid(rec) if rec["status"] == "running" else None
        if pid is not None:
            live.update(_proc_usage(pid))
            live["health"] = health(rec["host"], rec["port"])
            gpu = _gpu_memory_by_pid().get(pid)
            if gpu is not None:
                live["device_bytes"] = gpu
            pred = rec["plan"]["host_bytes"]
            if live.get("rss_bytes") and pred:
                live["rss_vs_prediction"] = round(live["rss_bytes"] / pred, 3)
        rec["live"] = live
        return rec

    def wait_ready(self, name: str, timeout: float = 120.0) -> str:
        deadline = time.time() + timeout
        state = "unknown"
        while time.time() < deadline:
            rec = self.refresh(self.load(name))
            if rec["status"] != "running":
                return "exited"
            state = health(rec["host"], rec["port"])
            if state == "ok":
                return state
            time.sleep(0.5)
        return state

    def logs(self, name: str, lines: int = 50) -> str:
        rec = self.load(name)
        p = Path(rec["log"])
        if not p.is_file():
            return ""
        return "\n".join(p.read_text(errors="replace").splitlines()[-lines:])


def health(host: str, port: int) -> str:
    h = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    try:
        with urllib.request.urlopen(f"http://{h}:{port}/health", timeout=2) as r:
            return "ok" if r.status == 200 else f"http {r.status}"
    except urllib.error.HTTPError as e:
        return "loading" if e.code == 503 else f"http {e.code}"
    except (OSError, ValueError):
        return "unreachable"


def _proc_usage(pid: int) -> Doc:
    out: Doc = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                out["rss_bytes"] = int(line.split()[1]) * 1024
            elif line.startswith("Threads:"):
                out["threads"] = int(line.split()[1])
        out["cpus_allowed"] = sorted(os.sched_getaffinity(pid))
        stat = Path(f"/proc/{pid}/stat").read_text()
        f = stat[stat.rfind(")") + 2 :].split()
        out["cpu_seconds"] = round((int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK"), 2)
    except (OSError, ValueError, IndexError):
        pass
    return out


def _gpu_memory_by_pid() -> dict[int, int]:
    import shutil

    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    try:
        r = subprocess.run(
            [exe, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            out[int(parts[0])] = int(parts[1]) * MiB
    return out


def resolve_model(layout: Layout, ref: str) -> Path:
    """A path, or a model name inside the models directory (with or without .gguf)."""
    p = Path(ref).expanduser()
    if p.exists():
        return p.resolve()
    for cand in (layout.models_dir / ref, layout.models_dir / f"{ref}.gguf"):
        if cand.exists():
            return cand.resolve()
    raise RuntimeError_(f"model {ref!r} not found (path, or name in {layout.models_dir})")


def list_models(layout: Layout) -> list[Doc]:
    out = []
    d = layout.models_dir
    for p in sorted(d.glob("*.gguf")) + sorted(x for x in d.glob("*") if (x / "config.json").is_file()):
        out.append({"name": p.name, "path": str(p), "bytes": p.stat().st_size if p.is_file() else None})
    return out


def as_dict(spec: StartSpec) -> Doc:
    return asdict(spec)
