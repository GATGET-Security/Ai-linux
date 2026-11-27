# SPDX-License-Identifier: MIT
"""Autotuning foundation: candidate generation and safe adoption on the existing measurement stack.

Loop (self-optimization.md, ADR-0008 decision rules), per (hardware profile, engine build, model,
objective):

1. **Candidates** from capabilities x model analysis x plan: GPU layer split / MoE expert split around
   the predicted fit, threads, flash attention, micro-batch (prefill), and — only on request — lossy
   KV-cache types (quality gate mandatory) and experimental CPU placements (H-H-01/H-K-09/H-K-11).
   Every candidate is labeled ``lossless`` (exact algorithms; floating-point order may differ) or
   ``lossy``; predicted-infeasible candidates are dropped, failed runs count as infeasible.
2. **Screening** campaign (``purpose: screening``): baseline = engine defaults (what the runtime does
   without tuning) vs all candidates, RMIT, BH-corrected. Screening never adopts.
3. **Confirmation** campaign with fresh, independently ordered runs: baseline vs the screening winner.
4. **Adoption** only if the primary metric is a significant improvement beyond the threshold, no guard
   metric regresses, no run of the winner failed and, for lossy candidates, the KLD quality gate shows
   no detectable quality change. Otherwise the baseline (engine default) stays in force.
5. **Verification** (``verify``) re-runs baseline vs adopted later (e.g. after a driver or engine
   update); if the advantage is gone the record is reverted to the baseline.

Campaigns, statistics, store and quality gate are the existing ``ailx-bench`` components; this module
only decides *what* to measure and *what to do* with the analysis. Records are sealed documents
(``ailx.tuning-record/v0``) that point to the campaign and analysis IDs in the store.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ailx import campaign
from ailx.compute.model import LOSSLESS_KV
from ailx.compute.planner import Request, Split, _fits, memory_for, plan
from ailx.core import Doc, canonical_bytes, sha256_hex, utcnow, validate
from ailx.store import Store
from ailx.system.engines import EngineManager
from ailx.system.paths import Layout

OBJECTIVES = {
    "decode": ("tg_tokens_per_s", ["pp_tokens_per_s"]),
    "prefill": ("pp_tokens_per_s", ["tg_tokens_per_s"]),
}
# bench parameter -> runtime (llama-server) engine_config key
BENCH_TO_SERVER = {
    "n_gpu_layers": "n_gpu_layers",
    "n_cpu_moe": "n_cpu_moe",
    "threads": "threads",
    "flash_attn": "flash_attn",
    "batch_size": "batch_size",
    "ubatch_size": "ubatch_size",
    "cache_type_k": "cache_type_k",
    "cache_type_v": "cache_type_v",
}


class TuningError(RuntimeError):
    pass


@dataclass
class TuneSpec:
    model: str
    objective: str = "decode"
    n_prompt: int = 512
    n_gen: int = 128
    repetitions: int = 3
    screening_blocks: int = 3
    confirm_blocks: int = 5
    max_candidates: int = 6
    e_min: float = 0.02
    alpha: float = 0.05
    seed: int = 4242
    allow_lossy: bool = False
    include_experimental: bool = False
    quality_reference: str | None = None  # reference.json from 'ailx-bench quality reference'
    calibration_id: str | None = None  # A/A calibration analysis for the noise floor
    device: str = "auto"


def tuning_key(hwp_id: str, install_id: str, model_id: str, objective: str) -> str:
    return hashlib.sha256(f"{hwp_id}|{install_id}|{model_id}|{objective}".encode()).hexdigest()[:24]


def _mask(cpus: list[int]) -> str:
    return hex(sum(1 << c for c in cpus))


def candidates(caps: Doc, analysis: Doc, spec: TuneSpec) -> tuple[Doc, list[Doc]]:
    """Baseline (engine defaults) and candidate arms with labels and predicted memory."""
    ctx = spec.n_prompt + spec.n_gen
    req = Request(ctx=ctx, device=spec.device, use_live=False)
    p = plan(caps, analysis, req)
    if not p["feasible"]:
        raise TuningError("model does not fit this machine even at the benchmark context; see 'ailx model plan'")
    n = analysis["hparams"]["n_layer"]
    on_gpu = p["device"] != "cpu"
    base_params: Doc = {}
    if on_gpu:
        base_params["fit_target"] = 1024  # mirror llama-server's default --fit (engine fits layers itself)
    else:
        base_params["n_gpu_layers"] = 0
    baseline = {
        "id": "engine-default",
        "description": "engine defaults (what 'ailx run start' does without tuning)",
        "param_overrides": {},
        "class": "lossless",
        "experimental": False,
        "rationale": "baseline",
    }
    gpu_cap = p["budget"]["device_bytes"]
    ram_cap = p["budget"]["ram_bytes"]
    flash = p["engine_config"]["flash_attn"] != "off" and p["backend"] != "cpu"
    cands: list[Doc] = []

    def add(cid: str, overrides: Doc, rationale: str, cls: str = "lossless", experimental: bool = False) -> None:
        if any(c["param_overrides"] == overrides for c in cands) or not overrides:
            return
        cands.append(
            {
                "id": cid,
                "description": rationale,
                "param_overrides": overrides,
                "class": cls,
                "experimental": experimental,
                "rationale": rationale,
            }
        )

    def fits(split: Split) -> bool:
        return _fits(memory_for(analysis, req, ctx, split, True, flash), gpu_cap, ram_cap)

    ec = p["engine_config"]
    if p["mode"] == "hybrid-layers":
        k = ec["n_gpu_layers"]
        for kk in (k, k - 2, k + 2):
            if 1 <= kk < n and (kk <= k or fits(Split(kk, 0, False))):
                add(f"ngl-{kk}", {"n_gpu_layers": kk}, f"explicit layer split: {kk} of {n} layers on the GPU")
    elif p["mode"] == "hybrid-moe":
        m = ec["n_cpu_moe"]
        for mm in (m, m + 2, m - 2):
            if 0 <= mm <= n and (mm >= m or fits(Split(n, mm, True))):
                add(f"ncmoe-{mm}", {"n_gpu_layers": n + 1, "n_cpu_moe": mm}, f"experts of the first {mm} layers on CPU")
    elif p["mode"] == "gpu-full":
        add("ngl-all", {"n_gpu_layers": n + 1}, "all layers explicitly on the GPU (no engine fit)")
    cpu = caps["cpu"]
    phys, logical = cpu["physical_cores"], cpu["logical_cpus"]
    cpu_bound = p["memory"]["host_read_per_token_bytes"] > 0 or spec.objective == "prefill" or not on_gpu
    if cpu_bound:
        for t in sorted({max(1, phys // 2), logical} - {phys}):
            add(f"threads-{t}", {"threads": t}, f"{t} threads (default: physical cores = {phys})")
    for fa in ("on", "off"):
        add(f"fa-{fa}", {"flash_attn": fa}, f"flash attention {fa} (default auto; exact attention either way)")
    if spec.objective == "prefill":
        for ub in (1024, 2048):
            add(f"ubatch-{ub}", {"ubatch_size": ub, "batch_size": max(2048, ub)}, f"micro-batch {ub}")
    if spec.allow_lossy:
        if not spec.quality_reference:
            raise TuningError("lossy candidates need a quality reference (ailx-bench quality reference)")
        add("kv-q8_0", {"cache_type_k": "q8_0", "cache_type_v": "q8_0"}, "KV cache q8_0 (LOSSY)", cls="lossy")
    if spec.include_experimental and cpu["asymmetric_llc"]:
        for d in cpu["llc_domains"]:
            add(
                f"place-{d['role']}",
                {"cpu_mask": _mask(d["cpus"]), "threads": d["physical_cores"]},
                f"EXPERIMENTAL: threads pinned to the {d['role']} LLC domain (H-H-01, H-K-09)",
                experimental=True,
            )
    lossless = [c for c in cands if c["class"] == "lossless" and not c["experimental"]]
    rest = [c for c in cands if c not in lossless]
    chosen = (lossless + rest)[: spec.max_candidates]
    return {"baseline": baseline, "base_params": base_params, "plan": p}, chosen


def _workload(analysis: Doc, model_path: str, binary: str, spec: TuneSpec, base_params: Doc, gpu: bool) -> Doc:
    wl = {
        "schema": "ailx.workload/v0",
        "name": f"tune-{spec.objective}-{analysis['id'][4:16]}",
        "version": 1,
        "type": "llm-single",
        "description": f"autotuning workload ({spec.objective}) for {analysis['name']}",
        "adapter": "llama-bench",
        "params": {"n_prompt": spec.n_prompt, "n_gen": spec.n_gen, "repetitions": spec.repetitions, **base_params},
        "model": {"path": model_path, "precision": analysis["weights"]["dominant_type"], "lossy_vs_reference": None},
        "runtime": {"binary": binary},
        "output_len_policy": "fixed",
        "metrics": [
            {"name": "tg_tokens_per_s", "unit": "tokens/s", "better": "higher"},
            {"name": "pp_tokens_per_s", "unit": "tokens/s", "better": "higher"},
        ],
        "warmup_runs": 1,
        "timeout_s": 3600,
        "telemetry": ["psi", "cpu", *(["nvml"] if gpu else [])],
    }
    validate(wl, "ailx.workload/v0")
    return wl


def _spec(title: str, purpose: str, wl: Doc, arms: list[Doc], spec: TuneSpec, blocks: int, seed: int) -> Doc:
    primary, guards = OBJECTIVES[spec.objective]
    return {
        "title": title,
        "purpose": purpose,
        "hypothesis_ids": ["H-R-12"],
        "workload": wl,
        "arms": [
            {"id": a["id"], "description": a["description"], "param_overrides": a["param_overrides"]} for a in arms
        ],
        "baseline_arm": "engine-default",
        "design": {"blocks": blocks, "runs_per_block": 1, "seed": seed},
        "primary_metric": primary,
        "guard_metrics": guards,
        "e_min": spec.e_min,
        "alpha": spec.alpha,
        "fdr": "bh" if len(arms) > 2 else "none",
        "equivalence_margin": None,
        "bootstrap_resamples": 2000,
        **({"calibration_id": spec.calibration_id} if spec.calibration_id else {}),
    }


def _verdicts(ana: Doc, arm: str) -> dict[str, Doc]:
    return {c["metric"]: c for c in ana["comparisons"] if c["arm"] == arm}


def _failed_runs(ana: Doc, store: Store, arm: str) -> int:
    return sum(1 for e in ana["excluded"] if store.get(e["id"]).get("arm_id") == arm)


def pick_winner(ana: Doc, store: Store, arms: list[Doc], spec: TuneSpec) -> Doc | None:
    """Screening: best primary point estimate beyond e_min/2 without guard regressions or failures."""
    primary, _ = OBJECTIVES[spec.objective]
    best, best_imp = None, spec.e_min / 2
    for a in arms:
        v = _verdicts(ana, a["id"])
        if primary not in v or _failed_runs(ana, store, a["id"]):
            continue
        if any(c["verdict"] == "regression" for m, c in v.items() if m != primary):
            continue
        imp = v[primary]["improvement"]
        if imp is not None and imp > best_imp:
            best, best_imp = a, imp
    return best


class Tuner:
    def __init__(self, layout: Layout, store: Store | None = None, log: Any = print) -> None:
        self.layout = layout
        self.store = store or Store(Path(os.environ.get("AILX_STORE") or layout.state_dir / "store"))
        self.dir = layout.state_dir / "tuning"
        self.log = log

    # ------------------------------------------------------------ records
    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> Doc | None:
        p = self._path(key)
        if not p.is_file():
            return None
        doc: Doc = json.loads(p.read_text())
        validate(doc, "ailx.tuning-record/v0")
        return doc

    def records(self) -> list[Doc]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:
                continue
        return out

    def _save(self, rec: Doc) -> Doc:
        body = {k: v for k, v in rec.items() if k not in ("id",)}
        rec = dict(body, id="tun-" + sha256_hex(canonical_bytes(body)))
        validate(rec, "ailx.tuning-record/v0")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(rec["key"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, self._path(rec["key"]))
        return rec

    def lookup(self, caps: Doc, analysis: Doc, objective: str = "decode") -> Doc | None:
        """Adopted record valid for this exact hardware profile, engine build and model — else None."""
        cur = EngineManager(self.layout).current("llama.cpp")
        if not cur:
            return None
        rec = self.get(tuning_key(caps["hardware_profile_id"], cur["install_id"], analysis["id"], objective))
        return rec if rec and rec["status"] == "adopted" else None

    # ------------------------------------------------------------ run
    def run(self, caps: Doc, analysis: Doc, model_path: str, spec: TuneSpec, dry_run: bool = False) -> Doc:
        if spec.objective not in OBJECTIVES:
            raise TuningError(f"unknown objective {spec.objective!r} ({', '.join(OBJECTIVES)})")
        em = EngineManager(self.layout)
        binary, manifest = em.binary("llama.cpp", "llama-bench")
        ctx, cands = candidates(caps, analysis, spec)
        gpu = ctx["plan"]["device"] != "cpu"
        wl = _workload(analysis, model_path, str(binary), spec, ctx["base_params"], gpu)
        key = tuning_key(caps["hardware_profile_id"], manifest["install_id"], analysis["id"], spec.objective)
        screening = _spec(
            f"autotune screening ({spec.objective}): {analysis['name']}",
            "screening",
            wl,
            [ctx["baseline"], *cands],
            spec,
            spec.screening_blocks,
            spec.seed,
        )
        if dry_run:
            return {
                "key": key,
                "plan": ctx["plan"],
                "baseline": ctx["baseline"],
                "candidates": cands,
                "screening_campaign_spec": screening,
                "runs": len(screening["arms"]) * spec.screening_blocks,
            }
        if not cands:
            raise TuningError("no candidates differ from the engine defaults on this machine")
        rec: Doc = {
            "schema": "ailx.tuning-record/v0",
            "key": key,
            "created_at": utcnow(),
            "hardware_profile_id": caps["hardware_profile_id"],
            "capabilities_id": caps["id"],
            "engine": {"engine": "llama.cpp", "install_id": manifest["install_id"], "variant": manifest.get("variant")},
            "model": {"id": analysis["id"], "name": analysis["name"], "path": model_path},
            "objective": spec.objective,
            "spec": asdict(spec),
            "baseline": {"id": "engine-default", "engine_config": {}},
            "candidates": [{k: c[k] for k in ("id", "param_overrides", "class", "experimental")} for c in cands],
            "status": "running",
            "adopted": None,
            "evidence": {"store": str(self.store.root)},
            "history": [],
        }
        n_runs = len(screening["arms"]) * spec.screening_blocks
        self.log(f"screening {len(cands)} candidate(s) against the engine defaults ({n_runs} runs)")
        cmp1 = campaign.plan(screening, self.store)
        campaign.run(cmp1["id"], self.store, log=self.log, use_nvml=gpu)
        ana1 = campaign.analyze(cmp1["id"], self.store)
        rec["evidence"].update(
            calibrated=bool(spec.calibration_id),
            screening_campaign=cmp1["id"],
            screening_analysis=ana1["id"],
            measurement_kind=ana1["measurement_kind"],
        )
        winner = pick_winner(ana1, self.store, cands, spec)
        if winner is None:
            rec.update(status="baseline", decision="no candidate beat the engine defaults in screening")
            rec["history"].append({"at": utcnow(), "event": "baseline-kept", "analysis": ana1["id"]})
            return self._save(rec)
        self.log(f"confirming {winner['id']} with fresh, independently ordered runs")
        confirm = _spec(
            f"autotune confirmation ({spec.objective}): {winner['id']} vs engine defaults",
            "confirmatory",
            wl,
            [ctx["baseline"], winner],
            spec,
            spec.confirm_blocks,
            spec.seed + 1,
        )
        cmp2 = campaign.plan(confirm, self.store)
        campaign.run(cmp2["id"], self.store, log=self.log, use_nvml=gpu)
        ana2 = campaign.analyze(cmp2["id"], self.store)
        rec["evidence"].update(confirm_campaign=cmp2["id"], confirm_analysis=ana2["id"])
        decision, ok = self._decide(ana2, winner, spec)
        quality = None
        if ok and winner["class"] == "lossy":
            quality = self._quality_gate(model_path, winner, spec.quality_reference or "")
            if quality["verdict"] != "no-detectable-quality-change":
                ok, decision = False, f"quality gate: {quality['verdict']} ({quality.get('detail')})"
        rec["evidence"]["quality_gate"] = quality
        rec["evidence"]["calibrated"] = bool(spec.calibration_id)
        if not spec.calibration_id:
            decision += " (uncalibrated: no A/A noise floor referenced; threshold = e_min only)"
        primary, _ = OBJECTIVES[spec.objective]
        effect = _verdicts(ana2, winner["id"]).get(primary)
        rec["decision"] = decision
        if ok:
            cfg = {BENCH_TO_SERVER[k]: v for k, v in winner["param_overrides"].items() if k in BENCH_TO_SERVER}
            rec["status"] = "adopted"
            rec["adopted"] = {
                "candidate": winner["id"],
                "class": winner["class"],
                "experimental": winner["experimental"],
                "engine_config": cfg,
                "placement_cpu_mask": winner["param_overrides"].get("cpu_mask"),
                "effect": {k: effect[k] for k in ("ratio", "improvement", "improvement_ci", "verdict")}
                if effect
                else None,
            }
            rec["history"].append(
                {"at": utcnow(), "event": "adopted", "candidate": winner["id"], "analysis": ana2["id"]}
            )
        else:
            rec["status"] = "baseline"
            rec["history"].append({"at": utcnow(), "event": "baseline-kept", "analysis": ana2["id"], "why": decision})
        return self._save(rec)

    def _decide(self, ana: Doc, winner: Doc, spec: TuneSpec) -> tuple[str, bool]:
        primary, _ = OBJECTIVES[spec.objective]
        v = _verdicts(ana, winner["id"])
        if _failed_runs(ana, self.store, winner["id"]):
            return "winner had failed runs (treated as infeasible)", False
        if primary not in v or v[primary]["verdict"] != "improvement":
            return f"confirmation: primary metric {v.get(primary, {}).get('verdict', 'missing')}", False
        regress = [m for m, c in v.items() if m != primary and c["verdict"] == "regression"]
        if regress:
            return f"confirmation: guard regression in {', '.join(regress)}", False
        return "confirmed improvement without guard regressions", True

    def _quality_gate(self, model_path: str, winner: Doc, reference: str) -> Doc:
        """KLD gate for lossy candidates: candidate engine flags vs the reference logits (same model file)."""
        from ailx.quality.kld import evaluate

        binary, _ = EngineManager(self.layout).binary("llama.cpp", "llama-perplexity")
        o = winner["param_overrides"]
        extra = []
        for k, flag in (("cache_type_k", "-ctk"), ("cache_type_v", "-ctv")):
            if k in o:
                extra += [flag, o[k]]
        return evaluate(str(binary), model_path, reference, extra_args=extra)

    def calibrate(self, caps: Doc, analysis: Doc, model_path: str, spec: TuneSpec, blocks: int = 8) -> Doc:
        """A/A calibration on exactly the tuning workload: noise floor for ``run(..., calibration_id)``."""
        binary, _ = EngineManager(self.layout).binary("llama.cpp", "llama-bench")
        ctx, _ = candidates(caps, analysis, spec)
        gpu = ctx["plan"]["device"] != "cpu"
        wl = _workload(analysis, model_path, str(binary), spec, ctx["base_params"], gpu)
        arms = [
            {"id": "engine-default", "description": "engine defaults", "param_overrides": {}},
            {"id": "engine-default-2", "description": "engine defaults (identical arm)", "param_overrides": {}},
        ]
        aa = _spec(
            f"autotune A/A calibration ({spec.objective})", "aa-calibration", wl, arms, spec, blocks, spec.seed + 99
        )
        aa.pop("calibration_id", None)
        cmp = campaign.plan(aa, self.store)
        campaign.run(cmp["id"], self.store, log=self.log, use_nvml=gpu)
        return campaign.analyze(cmp["id"], self.store)

    # ------------------------------------------------------------ verify / reset
    def verify(self, caps: Doc, analysis: Doc, model_path: str, key: str, blocks: int = 3) -> Doc:
        rec = self.get(key)
        if not rec or rec["status"] != "adopted":
            raise TuningError("nothing adopted under this key")
        spec = TuneSpec(**{k: v for k, v in rec["spec"].items() if k in TuneSpec.__dataclass_fields__})
        binary, manifest = EngineManager(self.layout).binary("llama.cpp", "llama-bench")
        if (
            manifest["install_id"] != rec["engine"]["install_id"]
            or caps["hardware_profile_id"] != rec["hardware_profile_id"]
        ):
            return self.reset(key, "engine build or hardware profile changed since adoption")
        ctx, cands = candidates(caps, analysis, spec)
        winner = next((c for c in cands if c["id"] == rec["adopted"]["candidate"]), None)
        if winner is None:
            return self.reset(key, "adopted candidate is no longer generated for this machine")
        wl = _workload(analysis, model_path, str(binary), spec, ctx["base_params"], ctx["plan"]["device"] != "cpu")
        cspec = _spec(f"autotune verification: {winner['id']}", "confirmatory", wl, [ctx["baseline"], winner], spec,
                      blocks, spec.seed + 7 + len(rec["history"]))  # fmt: skip
        cmp = campaign.plan(cspec, self.store)
        campaign.run(cmp["id"], self.store, log=self.log, use_nvml=ctx["plan"]["device"] != "cpu")
        ana = campaign.analyze(cmp["id"], self.store)
        decision, ok = self._decide(ana, winner, spec)
        rec = copy.deepcopy(rec)
        if ok:
            rec["history"].append({"at": utcnow(), "event": "verified", "analysis": ana["id"]})
            return self._save(rec)
        rec["history"].append({"at": utcnow(), "event": "reverted", "analysis": ana["id"], "why": decision})
        rec.update(status="reverted", decision=f"verification failed: {decision}")
        return self._save(rec)

    def reset(self, key: str, why: str = "operator reset") -> Doc:
        rec = self.get(key)
        if not rec:
            raise TuningError(f"no tuning record {key}")
        rec = copy.deepcopy(rec)
        rec["history"].append({"at": utcnow(), "event": "reverted", "why": why})
        rec.update(status="reverted", decision=why)
        return self._save(rec)


def lossless_only(cfg: Doc) -> bool:
    return all(cfg.get(k, "f16") in LOSSLESS_KV for k in ("cache_type_k", "cache_type_v"))
