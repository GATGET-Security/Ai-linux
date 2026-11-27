# SPDX-License-Identifier: MIT
"""Regenerate the language-neutral conformance corpus in ``schemas/fixtures``.

Valid documents are produced by the reference implementation from constructed inputs
(fake sysfs tree, simulated synthetic campaign); invalid documents are targeted mutations.
Run: python tests/make_schema_fixtures.py
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakesys import build_two_ccd_desktop

from ailx import campaign
from ailx.probe import collect_fingerprint, collect_hardware_profile
from ailx.probe.fingerprint import HashCache
from ailx.store import Store

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "schemas" / "fixtures"


def dump(kind: str, schema: str, name: str, doc: object) -> None:
    d = OUT / kind / schema
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        root = build_two_ccd_desktop(t / "root")
        hwp = collect_hardware_profile(root, use_nvml=False)
        fp = collect_fingerprint(hwp["id"], root, use_nvml=False, hash_cache=HashCache(t / "h.json"))
        fp = copy.deepcopy(fp)
        st = Store(t / "store")
        spec = json.loads((REPO / "workloads/campaigns/synthetic-effect-5pct.json").read_text())
        spec["design"] = {"blocks": 2, "runs_per_block": 2, "seed": 3}
        spec["bootstrap_resamples"] = 500
        spec["workload"] = str(REPO / "workloads/smoke/synthetic-simulated.json")
        cmp = campaign.plan(spec, st)
        campaign.run(cmp["id"], st, log=lambda *_: None, use_nvml=False)
        ana = campaign.analyze(cmp["id"], st)
        res = st.results_for(cmp["id"])[0]

    valid = {
        "hardware-profile.v0": {"two-ccd-desktop": hwp},
        "environment-fingerprint.v0": {"ubuntu-26.04-like": fp},
        "campaign.v0": {"synthetic-5pct": cmp},
        "result.v0": {"synthetic-run": res},
        "analysis.v0": {"synthetic-5pct": ana},
        "workload.v0": {p.stem: json.loads(p.read_text()) for p in (REPO / "workloads/smoke").glob("*.json")},
        "knob.v0": {
            "thp-mode": {
                "schema": "ailx.knob/v0",
                "id": "kernel.mm.thp.enabled",
                "description": "Transparent huge pages mode",
                "level": "kernel-runtime",
                "mechanism": {"sysfs": "/sys/kernel/mm/transparent_hugepage/enabled"},
                "domain": {"enum": ["always", "madvise", "never"]},
                "tier": "T1",
                "reversibility": "runtime",
                "security_class": "allowed",
                "quality_class": "n/a",
                "evidence": ["K-32"],
                "validation_status": "authored",
            }
        },
        "sample-stream.v0": {
            "header": {
                "schema": "ailx.sample-stream/v0",
                "kind": "header",
                "sampler": "nvml",
                "version": "0.1.0",
                "clock": "CLOCK_MONOTONIC",
                "available": True,
                "interval_s": 0.1,
                "metrics": [{"name": "gpu0.energy_mj", "unit": "mJ", "type": "counter"}],
            },
            "sample": {"kind": "sample", "t_ns": 123456789, "values": {"gpu0.energy_mj": 1234567}},
            "event": {"kind": "event", "t_ns": 123456999, "name": "throttle", "attrs": {"reason": "thermal"}},
        },
    }
    import tomllib

    profiles_dir = REPO / "python/src/ailx/system/data/profiles"
    valid["profile.v0"] = {p.stem: tomllib.loads(p.read_text()) for p in sorted(profiles_dir.glob("*.toml"))}
    valid["engine-manifest.v0"] = {
        "tiny-cmake": {
            "schema": "ailx.engine-manifest/v0",
            "engine": "llama.cpp",
            "install_id": "98cc730cc8f8-cpu",
            "kind": "cmake",
            "version": "98cc730cc8f8f798cf83d22c23a072c205b0e510",
            "variant": "cpu",
            "licence": "MIT",
            "source": {
                "url": "https://github.com/ggml-org/llama.cpp",
                "commit": "98cc730cc8f8f798cf83d22c23a072c205b0e510",
            },
            "build": {"system": "cmake", "flags": ["-DGGML_NATIVE=OFF"], "targets": ["llama-bench"]},
            "toolchain": {"cmake": "cmake version 3.28.3"},
            "artifacts": [{"path": "bin/llama-bench", "sha256": "0" * 64, "size": 1}],
            "installed_at": "2026-11-27T00:00:00.000000Z",
            "host": {},
            "notes": [],
        }
    }
    for schema, docs in valid.items():
        for name, doc in docs.items():
            dump("valid", schema, name, doc)

    def drop(d: dict, key: str) -> dict:  # type: ignore[type-arg]
        d = copy.deepcopy(d)
        d.pop(key)
        return d

    def setk(d: dict, key: str, val: object) -> dict:  # type: ignore[type-arg]
        d = copy.deepcopy(d)
        d[key] = val
        return d

    invalid = {
        "hardware-profile.v0": {
            "missing-cpu": drop(hwp, "cpu"),
            "bad-id": setk(hwp, "id", "hwp-xyz"),
            "extra-field": setk(hwp, "surprise", 1),
        },
        "environment-fingerprint.v0": {"missing-security": drop(fp, "security")},
        "campaign.v0": {"one-arm": setk(cmp, "arms", cmp["arms"][:1]), "bad-fdr": setk(cmp, "fdr", "bonferroni")},
        "result.v0": {
            "bad-kind": setk(res, "measurement_kind", "estimated"),
            "bad-status": setk(res, "status", "great"),
        },
        "analysis.v0": {"bad-verdict": setk(ana, "comparisons", [dict(ana["comparisons"][0], verdict="equivalent")])},
        "workload.v0": {"no-metrics": setk(valid["workload.v0"]["synthetic-simulated"], "metrics", [])},
        "knob.v0": {"bad-security-class": setk(valid["knob.v0"]["thp-mode"], "security_class", "probably-fine")},
        "profile.v0": {
            "missing-rationale": setk(
                valid["profile.v0"]["inference"],
                "settings",
                [{"knob": "vm.swappiness", "value": "10", "experimental": True}],
            )
        },
        "engine-manifest.v0": {
            "bad-sha": setk(
                valid["engine-manifest.v0"]["tiny-cmake"], "artifacts", [{"path": "bin/x", "sha256": "xyz", "size": 1}]
            )
        },
        "sample-stream.v0": {"wrong-clock": setk(valid["sample-stream.v0"]["header"], "clock", "CLOCK_REALTIME")},
    }
    for schema, docs in invalid.items():
        for name, doc in docs.items():
            dump("invalid", schema, name, doc)
    print(f"fixtures written to {OUT}")


def compute_fixtures() -> None:
    """Compute-engine schemas (capabilities, model analysis, runtime instance, tuning record)."""
    from compute_fix import dense_8b, workstation_caps

    from ailx.compute.planner import Request, plan

    def setk(d: dict, key: str, val: object) -> dict:  # type: ignore[type-arg]
        out = copy.deepcopy(d)
        out[key] = val
        return out

    with tempfile.TemporaryDirectory() as tmp:
        caps = workstation_caps(build_two_ccd_desktop(Path(tmp) / "desktop"))
    caps["collected_at"] = "2026-11-27T00:00:00Z"
    ana = dense_8b()
    p = plan(caps, ana, Request(ctx=8192))
    inst = {
        "schema": "ailx.runtime-instance/v0",
        "name": "chat",
        "cmd": ["/var/lib/ailx/engines/llama.cpp/98cc730cc8f8-cuda/bin/llama-server", "-m", "/models/m.gguf"],
        "host": "127.0.0.1",
        "port": 8080,
        "model": {"path": "/models/m.gguf", "id": ana["id"], "name": ana["name"]},
        "engine": {"engine": "llama.cpp", "install_id": "98cc730cc8f8-cuda", "binary": "/x/llama-server"},
        "config_source": "engine-default",
        "engine_config": p["engine_config"],
        "explicit_settings": {},
        "priority": "interactive",
        "placement": {"policy": "none", "cpus": None, "experimental": False},
        "process": {"nice": 0, "oom_score_adj": 0},
        "plan": {
            "mode": p["mode"],
            "device": p["device"],
            "backend": p["backend"],
            "host_bytes": p["memory"]["host_bytes"],
            "device_bytes": p["memory"]["device_bytes"],
            "estimate": p["estimate"],
        },
        "admission": {"admitted": True, "reasons": []},
        "pid": 4242,
        "start_ticks": 123456,
        "uid": 1000,
        "log": "/var/lib/ailx/runtime/logs/chat.log",
        "status": "running",
        "started_at": "2026-11-27T00:00:00Z",
        "stopped_at": None,
        "stop_reason": None,
    }
    tun = {
        "schema": "ailx.tuning-record/v0",
        "id": "tun-" + "0" * 64,
        "key": "0" * 24,
        "created_at": "2026-11-27T00:00:00Z",
        "hardware_profile_id": caps["hardware_profile_id"],
        "capabilities_id": caps["id"],
        "engine": {"engine": "llama.cpp", "install_id": "98cc730cc8f8-cuda"},
        "model": {"id": ana["id"], "name": ana["name"], "path": "/models/m.gguf"},
        "objective": "decode",
        "spec": {"objective": "decode"},
        "baseline": {"id": "engine-default", "engine_config": {}},
        "candidates": [
            {"id": "fa-on", "param_overrides": {"flash_attn": "on"}, "class": "lossless", "experimental": False}
        ],
        "status": "adopted",
        "decision": "confirmed improvement without guard regressions (constructed fixture)",
        "adopted": {
            "candidate": "fa-on",
            "class": "lossless",
            "experimental": False,
            "engine_config": {"flash_attn": "on"},
            "placement_cpu_mask": None,
            "effect": {"ratio": 1.1, "improvement": 0.1, "improvement_ci": [0.05, 0.15], "verdict": "improvement"},
        },
        "evidence": {
            "store": "/var/lib/ailx/store",
            "confirm_analysis": "ana-" + "1" * 64,
            "calibrated": True,
            "measurement_kind": "hardware",
            "quality_gate": None,
        },
        "history": [{"at": "2026-11-27T00:00:00Z", "event": "adopted"}],
    }
    lossy = copy.deepcopy(tun)
    lossy["adopted"]["class"] = "lossy"  # lossy adoption without a passed quality gate must be rejected
    for kind, schema, name, doc in [
        ("valid", "capabilities.v0", "workstation-7950x3d-4090", caps),
        ("valid", "model-analysis.v0", "dense-8b-synthetic", ana),
        ("valid", "runtime-instance.v0", "running", inst),
        ("valid", "tuning-record.v0", "adopted-lossless", tun),
        ("invalid", "capabilities.v0", "bad-backend", setk(caps, "backends", [{"backend": "opencl"}])),
        ("invalid", "model-analysis.v0", "bad-format", setk(ana, "format", "onnx")),
        ("invalid", "runtime-instance.v0", "bad-priority", setk(inst, "priority", "realtime")),
        ("invalid", "tuning-record.v0", "lossy-without-quality-gate", lossy),
        (
            "invalid",
            "tuning-record.v0",
            "adopted-without-confirmation",
            setk(tun, "evidence", {"store": "/var/lib/ailx/store"}),
        ),
    ]:
        dump(kind, schema, name, doc)
    print("compute fixtures written")


if __name__ == "__main__":
    if sys.argv[1:] == ["--only", "compute"]:
        compute_fixtures()
    else:
        main()
        compute_fixtures()
