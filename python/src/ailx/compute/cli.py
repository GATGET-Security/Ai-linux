# SPDX-License-Identifier: MIT
"""CLI for the AI Compute Engine: ``ailx caps | model | recommend | run | tune``.

Every command has ``--json`` (global flag) for machine-readable output; errors in JSON mode are
printed as ``{"error": ..., ...}`` on stdout with a non-zero exit code. Commands that start or
measure something have ``--dry-run``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ailx.compute.capabilities import collect_capabilities
from ailx.compute.gguf import GGUFError
from ailx.compute.model import analyze
from ailx.compute.planner import Request, plan
from ailx.compute.runtime import (
    PRIORITIES,
    AdmissionError,
    RuntimeError_,
    RuntimeManager,
    StartSpec,
    list_models,
    resolve_model,
    runtime_config,
)
from ailx.compute.tuning import OBJECTIVES, Tuner, TuneSpec, TuningError
from ailx.core import Doc
from ailx.system.engines import EngineError
from ailx.system.paths import Layout

GiB = 1024**3
MiB = 1024**2
ERRORS = (RuntimeError_, TuningError, GGUFError, EngineError, FileNotFoundError, ValueError)


def _out(a: argparse.Namespace, obj: Any, human: str) -> None:
    print(json.dumps(obj, indent=2, default=str) if a.json else human)


def _g(x: float | None) -> str:
    return "n/a" if x is None else f"{x / GiB:.2f} GiB"


def _caps(a: argparse.Namespace, lay: Layout) -> Doc:
    return collect_capabilities(lay, use_nvml=not a.no_nvml)


# ---------------------------------------------------------------- caps


def cmd_caps(a: argparse.Namespace, lay: Layout) -> int:
    c = _caps(a, lay)
    cpu = c["cpu"]
    lines = [
        f"Capabilities {c['id'][:16]} (hardware profile {c['hardware_profile_id'][:16]})",
        f"CPU     {cpu['model']}: {cpu['physical_cores']} cores / {cpu['logical_cpus']} threads, "
        f"ISA {cpu['isa']['level']}, AI extensions: {', '.join(cpu['isa']['ai_extensions']) or 'none'}",
    ]
    for d in cpu["llc_domains"]:
        role = f" [{d['role']}]" if d["role"] else ""
        freq = f", max {d['max_khz'] / 1e6:.2f} GHz" if d["max_khz"] else ""
        lines.append(
            f"  LLC {d['id']}{role}: {(d['l3_bytes'] or 0) // MiB} MiB, {d['physical_cores']} cores, "
            f"CPUs {_cpulist(d['cpus'])}{freq}, NUMA {d['numa_node']}"
        )
    m = c["memory"]
    lines.append(
        f"Memory  {_g(m['total_bytes'])} (available now {_g(c['live'].get('memory_available_bytes'))}), "
        f"swap {_g(m['swap_total_bytes'])}, NUMA nodes {len(c['numa'])}, bandwidth: {m['bandwidth']['provenance']}"
    )
    for d in c["accelerators"]:
        mem = d["memory"]
        bw = mem["bandwidth"]
        bws = f"{bw['bytes_per_s'] / 1e9:.0f} GB/s ({bw['provenance']})" if bw["bytes_per_s"] else "bandwidth unknown"
        free = c["live"].get(d["id"], {}).get("vram_free_bytes")
        lines.append(
            f"{d['kind'].upper():7} {d['id']} {d['name'] or ''} [{d['source']}]: {mem['kind']} {_g(mem['total_bytes'])}"
            f" (free {_g(free)}), {bws}; driver {d['driver']} {d['driver_version'] or ''}"
            f"{', CC ' + d['compute_capability'] if d.get('compute_capability') else ''}; "
            f"backends: {', '.join(d['backends']) or 'none'}"
        )
    if not c["accelerators"]:
        lines.append("GPU     none detected")
    st = c["storage"]
    fs = st["filesystem"] or {}
    lines.append(
        f"Models  {st['models_dir']} on {fs.get('source', '?')} ({fs.get('fstype', '?')}), "
        f"free {_g(c['live'].get('models_dir_free_bytes'))}"
    )
    lines.append("Backends (usable = device + driver stack + installed engine build):")
    for b in c["backends"]:
        lines.append(
            f"  {'✔' if b['usable'] else '✘'} {b['backend']:7} devices {', '.join(b['devices']) or '-'}; "
            f"builds {', '.join(b['engine_builds']) or '-'}"
        )
    if c["unavailable"]:
        lines.append("Unknown / unavailable:")
        lines += [f"  - {k}: {v}" for k, v in sorted(c["unavailable"].items())]
    _out(a, c, "\n".join(lines))
    return 0


def _cpulist(cpus: list[int]) -> str:
    out, start = [], None
    for i, c in enumerate(cpus):
        if start is None:
            start = c
        if i + 1 == len(cpus) or cpus[i + 1] != c + 1:
            out.append(str(start) if start == c else f"{start}-{c}")
            start = None
    return ",".join(out)


# ---------------------------------------------------------------- model / recommend


def _request(a: argparse.Namespace, lay: Layout) -> Request:
    cfg = runtime_config(lay)
    kv = getattr(a, "kv", None) or "f16"
    return Request(
        ctx=getattr(a, "ctx", None),
        parallel=getattr(a, "parallel", 1) or 1,
        cache_type_k=kv,
        cache_type_v=kv,
        allow_lossy=getattr(a, "allow_lossy", False),
        device=getattr(a, "device", "auto") or "auto",
        reserve_ram_bytes=int(cfg["reserve_ram_mib"]) * MiB,
        reserve_vram_bytes=int(cfg["reserve_vram_mib"]) * MiB,
        use_live=not getattr(a, "no_live", False),
    )


def _plan_text(p: Doc) -> list[str]:
    m, ec = p["memory"], p["engine_config"]
    lines = [
        f"Placement  {p['mode']} on {p['device']} via {p['engine']}/{p['backend']}  [{p['quality']['label']}]",
        f"Memory     device {_g(m['device_bytes'])}, host {_g(m['host_bytes'])} (KV cache {_g(m['kv_bytes'])}) — "
        f"{p['estimate']}",
        f"Budget     host {_g(p['budget']['ram_bytes'])}, device {_g(p['budget']['device_bytes'])} "
        f"(+{p['budget']['fit_margin_bytes'] // MiB} MiB engine fit margin)",
        f"Config     ctx {ec['ctx_size']}, slots {ec.get('parallel')}, gpu layers {ec.get('n_gpu_layers')}, "
        f"cpu-moe {ec.get('n_cpu_moe')}, KV {ec.get('cache_type_k')}/{ec.get('cache_type_v')}",
        f"Max ctx    fully on GPU {p['max_ctx']['gpu_full']}, CPU only {p['max_ctx']['cpu']}",
    ]
    b = p["bottlenecks"]
    bound = b["decode_upper_bound_tokens_per_s"]
    lines.append("Bottleneck " + "; ".join(b["classes"]))
    lines.append(f"Decode     {'≤ ' + str(bound) + ' tokens/s (' + b['formula'] + ')' if bound else b['formula']}")
    lines += [f"Action     {x}" for x in p["actions"]]
    lines += [f"Suggest    {x}" for x in p.get("suggestions", [])]
    lines += [f"Note       {x}" for x in p["notes"]]
    return lines


def cmd_model(a: argparse.Namespace, lay: Layout) -> int:
    if a.sub == "list":
        rows = list_models(lay)
        _out(
            a,
            {"models_dir": str(lay.models_dir), "models": rows},
            "\n".join([f"{lay.models_dir}:"] + [f"  {r['name']}  {_g(r['bytes'])}" for r in rows] or ["  (empty)"]),
        )
        return 0
    path = resolve_model(lay, a.model)
    ana = analyze(path)
    if a.sub == "inspect":
        w, hp = ana["weights"], ana["hparams"]
        lines = [
            f"Model      {ana['name']} ({ana['architecture']}, {ana['format']}) {ana['id'][:16]}",
            f"Params     {ana['params'] / 1e9:.2f} B, weights {_g(w['total_bytes'])}, "
            f"{w['bits_per_weight']} bits/weight, "
            f"dominant {w['dominant_type']}",
            "Types      " + ", ".join(f"{k} {_g(v)}" for k, v in w["by_type_bytes"].items()),
            f"Layers     {hp['n_layer']} x ~{_g(sum(w['per_layer_bytes']) / max(1, hp['n_layer']))}, "
            f"embd {hp['n_embd']}, "
            f"heads {hp['n_head']}/{hp['n_head_kv'][0] if hp['n_head_kv'] else '?'} (q/kv), vocab {hp['n_vocab']}",
            f"Context    trained {hp['n_ctx_train']}",
        ]
        if hp["n_expert"]:
            lines.append(
                f"MoE        {hp['n_expert']} experts, {hp['n_expert_used']} active; expert weights "
                f"{_g(sum(w['per_layer_expert_bytes']))}; active per token {_g(w['active_bytes_per_token'])}"
            )
        lines += [f"Note       {n}" for n in ana["notes"]]
        _out(a, ana, "\n".join(lines))
        return 0
    p = plan(_caps(a, lay), ana, _request(a, lay))
    _out(a, p, "\n".join(_plan_text(p)))
    return 0 if p["feasible"] else 2


def cmd_recommend(a: argparse.Namespace, lay: Layout) -> int:
    caps = _caps(a, lay)
    path = resolve_model(lay, a.model)
    ana = analyze(path)
    p = plan(caps, ana, _request(a, lay))
    rec = Tuner(lay, log=lambda *x: None).lookup(caps, ana, a.objective)
    config = "tuned" if rec else "engine-default"
    out: Doc = {
        "model": {"id": ana["id"], "name": ana["name"], "path": str(path)},
        "plan": p,
        "tuning": rec,
        "recommended_config": config,
        "commands": {
            "start": f"ailx run start <name> {path} --config {config}" + (f" --ctx {a.ctx}" if a.ctx else ""),
            "tune": f"ailx tune run {path} --objective {a.objective}",
        },
    }
    lines = _plan_text(p)
    if rec:
        ad = rec["adopted"]
        eff = ad["effect"] or {}
        lines.append(
            f"Tuned      {ad['candidate']} adopted ({ad['class']}): {ad['engine_config']}, "
            f"{a.objective} ratio {eff.get('ratio')} — evidence {rec['evidence'].get('confirm_analysis', '')[:16]}"
        )
    else:
        lines.append(
            f"Tuned      no adopted tuning for this machine/engine/model ({a.objective}); engine defaults apply"
        )
    lines += [f"Run        {out['commands']['start']}", f"Tune       {out['commands']['tune']}"]
    _out(a, out, "\n".join(lines))
    return 0 if p["feasible"] else 2


# ---------------------------------------------------------------- run


def cmd_run(a: argparse.Namespace, lay: Layout) -> int:
    rm = RuntimeManager(lay)
    if a.sub == "list":
        rows = rm.instances()
        lines = [
            f"{r['name']:16} {r['status']:8} pid {r['pid']:>7} port {r['port']:>5} {r['priority']:11} "
            f"{r['plan']['mode']:13} {Path(r['model']['path']).name}"
            for r in rows
        ]
        _out(a, rows, "\n".join(lines) or "no instances")
        return 0
    if a.sub == "status":
        st = rm.status(a.name)
        lv = st["live"]
        lines = [
            f"{st['name']}: {st['status']} (pid {st['pid']}, http://{st['host']}:{st['port']}, "
            f"health {lv.get('health')})",
            f"model {st['model']['path']}  engine {st['engine']['install_id']}  config {st['config_source']}",
            f"plan {st['plan']['mode']} on {st['plan']['device']}: predicted host {_g(st['plan']['host_bytes'])}, "
            f"device {_g(st['plan']['device_bytes'])}",
            f"live RSS {_g(lv.get('rss_bytes'))} (x{lv.get('rss_vs_prediction')} of prediction), threads "
            f"{lv.get('threads')}, CPU {lv.get('cpu_seconds')} s, GPU memory {_g(lv.get('device_bytes'))}",
            f"priority {st['priority']} (nice {st['process']['nice']}, "
            f"oom_score_adj {st['process']['oom_score_adj']}), "
            f"placement {st['placement']['policy']}{' (experimental)' if st['placement']['experimental'] else ''}",
        ]
        _out(a, st, "\n".join(lines))
        return 0
    if a.sub == "logs":
        print(rm.logs(a.name, a.lines))
        return 0
    if a.sub == "stop":
        r = rm.stop(a.name, timeout=a.timeout)
        _out(a, r, f"{a.name}: {r['status']} ({r['stop_reason']})")
        return 0
    if a.sub == "rm":
        rm.remove(a.name)
        _out(a, {"removed": a.name}, f"{a.name}: removed")
        return 0
    caps = _caps(a, lay)
    tuned = None
    config = a.config
    if config in ("tuned", "auto"):
        ana = analyze(resolve_model(lay, a.model))
        rec = Tuner(lay, log=lambda *x: None).lookup(caps, ana, a.objective)
        if rec:
            tuned, config = rec["adopted"]["engine_config"], "tuned"
            mask = rec["adopted"].get("placement_cpu_mask")
            if mask and a.placement == "none":  # experimental placement was part of what was measured
                a.placement = f"mask:{mask}"
        elif config == "tuned":
            raise RuntimeError_(
                "no adopted tuning for this machine/engine/model; run 'ailx tune run' or use --config auto"
            )
        else:
            config = "engine-default"
    spec = StartSpec(
        name=a.name,
        model=a.model,
        ctx=a.ctx,
        parallel=a.parallel,
        priority=a.priority,
        placement=a.placement,
        config=config,
        tuned=tuned,
        host=a.host,
        port=a.port,
        threads=a.threads,
        device=a.device,
        allow_lossy=a.allow_lossy,
        cache_type_k=a.kv or "f16",
        cache_type_v=a.kv or "f16",
    )
    try:
        r = rm.start(spec, caps, preempt=a.preempt, dry_run=a.dry_run)
    except AdmissionError as exc:
        d = exc.decision
        human = [f"ailx: {exc}"]
        if d["preemptible"]:
            human.append(f"lower-priority instances that could be stopped: {', '.join(d['preemptible'])} (--preempt)")
        human.append("alternatives: smaller --ctx, another model/quantization, or stop other instances")
        _out(a, {"error": str(exc), "admission": d}, "\n".join(human))
        return 3
    health = None
    if not a.dry_run and a.wait:
        health = rm.wait_ready(a.name, a.wait)
        r["health"] = health
    lines = [
        f"{a.name}: {r['status']} — {r['plan']['mode']} on {r['plan']['device']} ({r['config_source']}), "
        f"http://{r['host']}:{r['port']}",
        "command: " + " ".join(r["cmd"]),
        f"admission: admitted (needs host {_g(r['admission']['need']['ram_bytes'])}, device "
        f"{_g(r['admission']['need']['device_bytes'])})"
        + (f"; preempted {', '.join(r['admission']['preempted'])}" if r["admission"]["preempted"] else ""),
    ]
    if r["placement"]["experimental"]:
        lines.append(f"placement {r['placement']['policy']} is EXPERIMENTAL (not hardware-validated)")
    if health:
        lines.append(f"health: {health}")
    _out(a, r, "\n".join(lines))
    return 0 if health in (None, "ok") else 4


# ---------------------------------------------------------------- tune


def _tune_spec(a: argparse.Namespace) -> TuneSpec:
    return TuneSpec(
        model=a.model,
        objective=a.objective,
        n_prompt=a.n_prompt,
        n_gen=a.n_gen,
        repetitions=a.repetitions,
        screening_blocks=a.screening_blocks,
        confirm_blocks=a.confirm_blocks,
        max_candidates=a.max_candidates,
        e_min=a.e_min,
        allow_lossy=a.allow_lossy,
        include_experimental=a.include_experimental,
        quality_reference=a.quality_reference,
        calibration_id=a.calibration,
        device=a.device,
    )


def _record_text(r: Doc) -> str:
    lines = [
        f"{r['key']}  {r['status']:9} {r['objective']:8} {r['model']['name']}  engine {r['engine']['install_id']}",
        f"  decision: {r.get('decision', '-')}",
    ]
    if r.get("adopted"):
        ad = r["adopted"]
        eff = ad["effect"] or {}
        lines.append(
            f"  adopted {ad['candidate']} ({ad['class']}{', EXPERIMENTAL' if ad['experimental'] else ''}): "
            f"{ad['engine_config']}; ratio {eff.get('ratio')}, CI {eff.get('improvement_ci')}"
        )
    ev = r["evidence"]
    lines.append(
        f"  evidence ({ev.get('measurement_kind', '?')}): screening {ev.get('screening_analysis', '-')}, "
        f"confirmation {ev.get('confirm_analysis', '-')} in {ev['store']}"
    )
    return "\n".join(lines)


def cmd_tune(a: argparse.Namespace, lay: Layout) -> int:
    t = Tuner(lay, log=(lambda *x: None) if a.json else print)
    if a.sub == "list":
        rows = t.records()
        _out(a, rows, "\n".join(_record_text(r) for r in rows) or "no tuning records")
        return 0
    if a.sub in ("show", "reset"):
        r = t.get(a.key) if a.sub == "show" else t.reset(a.key)
        if r is None:
            raise TuningError(f"no tuning record {a.key}")
        _out(a, r, _record_text(r))
        return 0
    caps = _caps(a, lay)
    path = resolve_model(lay, a.model)
    ana = analyze(path)
    if a.sub == "calibrate":
        c = t.calibrate(caps, ana, str(path), _tune_spec(a), blocks=a.blocks)
        cal = c.get("calibration") or {}
        _out(
            a,
            c,
            f"A/A calibration {c['id']}\n  noise floor {cal.get('noise_floor')}\n  run CV {cal.get('run_cv')}\n"
            f"  false claims {cal.get('aa_false_claims')} (must be empty)\n  warnings {c['warnings']}\n"
            f"use: ailx tune run {path} --calibration {c['id']}",
        )
        return 0 if not cal.get("aa_false_claims") else 2
    if a.sub == "verify":
        key = a.key
        r = t.verify(caps, ana, str(path), key)
        _out(a, r, _record_text(r))
        return 0
    dry = a.sub == "plan" or a.dry_run
    r = t.run(caps, ana, str(path), _tune_spec(a), dry_run=dry)
    if dry:
        lines = [
            f"Tuning plan {r['key']} ({a.objective}) — {r['runs']} screening runs, then a confirmation campaign",
            f"Baseline   {r['baseline']['description']}",
        ]
        for c in r["candidates"]:
            tag = c["class"] + (", EXPERIMENTAL" if c["experimental"] else "")
            lines.append(f"Candidate  {c['id']:18} [{tag}] {c['param_overrides']} — {c['rationale']}")
        _out(a, r, "\n".join(lines))
        return 0
    _out(a, r, _record_text(r))
    return 0


# ---------------------------------------------------------------- parser


def register(sub: Any) -> None:
    sub.add_parser("caps", help="hardware capabilities for AI workloads (CPU/GPU/memory/backends)").set_defaults(
        fn=cmd_caps
    )

    def plan_args(x: argparse.ArgumentParser) -> None:
        x.add_argument("--ctx", type=int, help="context size (default: min(4096, trained context))")
        x.add_argument("--parallel", type=int, default=1, help="server slots")
        x.add_argument("--device", default="auto", help="auto | cpu | accelerator id (e.g. nvidia:0)")
        x.add_argument("--kv", choices=["f16", "bf16", "q8_0", "q4_0"], help="KV cache type (q*: LOSSY)")
        x.add_argument("--allow-lossy", action="store_true", help="allow lossy KV-cache types")
        x.add_argument("--no-live", action="store_true", help="plan against totals, ignore current free memory")

    mo = sub.add_parser("model", help="model analysis and placement planning").add_subparsers(dest="sub", required=True)
    mo.add_parser("list").set_defaults(fn=cmd_model)
    x = mo.add_parser("inspect", help="read GGUF / safetensors metadata (header only)")
    x.add_argument("model")
    x.set_defaults(fn=cmd_model)
    x = mo.add_parser("plan", help="placement, memory, engine and bottlenecks on this machine")
    x.add_argument("model")
    plan_args(x)
    x.set_defaults(fn=cmd_model)

    x = sub.add_parser("recommend", help="plan + adopted tuning + the command to run a model")
    x.add_argument("model")
    x.add_argument("--objective", choices=sorted(OBJECTIVES), default="decode")
    plan_args(x)
    x.set_defaults(fn=cmd_recommend)

    ru = sub.add_parser("run", help="managed inference servers").add_subparsers(dest="sub", required=True)
    x = ru.add_parser("start", help="start a llama-server instance after admission control")
    x.add_argument("name")
    x.add_argument("model")
    plan_args(x)
    x.add_argument("--priority", choices=list(PRIORITIES), default="interactive")
    x.add_argument(
        "--placement",
        default="none",
        help="none | large-cache | frequency | llc:<id> | numa:<node> (non-none: EXPERIMENTAL)",
    )
    x.add_argument(
        "--config",
        choices=["auto", "engine-default", "planned", "tuned"],
        default="auto",
        help="auto: adopted tuning if valid for this machine/engine/model, else engine defaults",
    )
    x.add_argument("--objective", choices=sorted(OBJECTIVES), default="decode", help="tuning record to use")
    x.add_argument("--host", help="listen address (default 127.0.0.1)")
    x.add_argument("--port", type=int)
    x.add_argument("--threads", type=int)
    x.add_argument("--preempt", action="store_true", help="stop lower-priority instances if the budget requires it")
    x.add_argument("--wait", type=float, default=0, metavar="SECONDS", help="wait until the server reports healthy")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_run)
    ru.add_parser("list").set_defaults(fn=cmd_run)
    for name in ("status", "logs", "stop", "rm"):
        x = ru.add_parser(name)
        x.add_argument("name")
        if name == "logs":
            x.add_argument("-n", "--lines", type=int, default=50)
        if name == "stop":
            x.add_argument("--timeout", type=float, default=15.0, help="seconds before SIGKILL")
        x.set_defaults(fn=cmd_run)

    tu = sub.add_parser("tune", help="autotuning: screening -> confirmation -> adoption").add_subparsers(
        dest="sub", required=True
    )
    for name in ("plan", "run", "calibrate"):
        x = tu.add_parser(name, help="show candidates (plan) / measure and decide (run) / A/A noise floor (calibrate)")
        x.add_argument("model")
        x.add_argument("--objective", choices=sorted(OBJECTIVES), default="decode")
        x.add_argument("--n-prompt", type=int, default=512)
        x.add_argument("--n-gen", type=int, default=128)
        x.add_argument("--repetitions", type=int, default=3)
        x.add_argument("--screening-blocks", type=int, default=3)
        x.add_argument("--confirm-blocks", type=int, default=5)
        x.add_argument("--max-candidates", type=int, default=6)
        x.add_argument("--e-min", type=float, default=0.02, help="minimum relevant effect (fraction)")
        x.add_argument("--allow-lossy", action="store_true", help="include LOSSY candidates (KV-cache quantization)")
        x.add_argument("--quality-reference", help="reference.json from 'ailx-bench quality reference' (lossy only)")
        x.add_argument("--include-experimental", action="store_true", help="include CPU placement candidates")
        x.add_argument("--calibration", help="A/A calibration analysis ID (noise floor)")
        x.add_argument("--device", default="auto")
        x.add_argument("--dry-run", action="store_true")
        x.add_argument("--blocks", type=int, default=8, help="calibrate: RMIT blocks of the A/A campaign")
        x.set_defaults(fn=cmd_tune)
    tu.add_parser("list").set_defaults(fn=cmd_tune)
    for name in ("show", "reset"):
        x = tu.add_parser(name)
        x.add_argument("key")
        x.set_defaults(fn=cmd_tune)
    x = tu.add_parser("verify", help="re-measure baseline vs adopted; revert if the advantage is gone")
    x.add_argument("model")
    x.add_argument("key")
    x.set_defaults(fn=cmd_tune)
