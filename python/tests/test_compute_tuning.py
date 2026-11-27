# SPDX-License-Identifier: MIT
"""Autotuning: candidate generation, screening -> confirmation -> adoption, verification and revert.

The pipeline uses the real campaign/statistics/store code. ``llama-bench`` is a stand-in that emits
llama-bench JSON with constructed speeds (FAKE_BENCH_FAST selects which flag is 'faster'); these
numbers exercise the decision logic only and are not performance data.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
from compute_fix import dense_70b, workstation_caps
from test_compute_model_planner import write_model

from ailx.compute.model import analyze
from ailx.compute.runtime import server_args
from ailx.compute.tuning import Tuner, TuneSpec, TuningError, candidates
from ailx.store import Store
from ailx.system.paths import Layout

FAKE_BENCH = textwrap.dedent(
    """\
    #!PYTHON
    import json, os, random, sys
    a = sys.argv[1:]
    def opt(flag, default):
        return a[a.index(flag) + 1] if flag in a else default
    reps = int(opt("-r", "3"))
    fast = [x for x in os.environ.get("FAKE_BENCH_FAST", "").split(",") if x]
    gain = 1.3 if any(opt(f.split("=")[0], None) == f.split("=")[1] for f in fast) else 1.0
    rnd = random.Random()
    def samples(base):
        return [base * gain * (1 + rnd.uniform(-0.005, 0.005)) for _ in range(reps)]
    pp, tg = int(opt("-p", "512")), int(opt("-n", "128"))
    out = [
        {"build_commit": "f", "build_number": 0, "n_prompt": pp, "n_gen": 0, "samples_ts": samples(1e3)},
        {"build_commit": "f", "build_number": 0, "n_prompt": 0, "n_gen": tg, "samples_ts": samples(1e2)},
    ]
    if fast and gain > 1 and os.environ.get("FAKE_BENCH_PP_ONLY"):
        out[1]["samples_ts"] = [s / gain / gain for s in out[1]["samples_ts"]]  # decode slower than baseline
    print(json.dumps(out))
    """
)


@pytest.fixture
def tuner(desktop_root: Path, tmp_path: Path) -> tuple[Tuner, dict, dict, str]:
    caps = workstation_caps(desktop_root)
    lay = Layout(
        root=desktop_root,
        state_dir=tmp_path / "state",
        engines_dir=desktop_root / "var/lib/ailx/engines",
        helper="/nonexistent",
        mode="user",
        models_dir=tmp_path / "models",
    )
    inst = lay.engines_dir / "llama.cpp" / "98cc730cc8f8-cuda"
    bench = inst / "bin" / "llama-bench"
    bench.write_text(FAKE_BENCH.replace("#!PYTHON", "#!" + sys.executable))
    bench.chmod(0o755)
    (inst / "manifest.json").write_text(
        json.dumps({"engine": "llama.cpp", "install_id": inst.name, "variant": "cuda", "kind": "cmake"})
    )
    link = lay.engines_dir / "llama.cpp" / "current"
    if not link.is_symlink():
        link.symlink_to(inst.name)
    model = write_model(tmp_path / "tiny.gguf")
    t = Tuner(lay, Store(tmp_path / "store"), log=lambda *a: None)
    return t, caps, analyze(model), str(model)


def test_candidates_hybrid_70b(desktop_root: Path) -> None:
    caps = workstation_caps(desktop_root)
    ctx, cands = candidates(caps, dense_70b(), TuneSpec(model="x", max_candidates=10))
    ids = [c["id"] for c in cands]
    k = ctx["plan"]["engine_config"]["n_gpu_layers"]
    assert ctx["plan"]["mode"] == "hybrid-layers" and f"ngl-{k}" in ids and f"ngl-{k - 2}" in ids
    assert {"threads-8", "threads-32", "fa-on", "fa-off"} <= set(ids)
    assert all(c["class"] == "lossless" and not c["experimental"] for c in cands)
    assert ctx["base_params"] == {"fit_target": 1024}  # baseline mirrors the server's engine fit
    assert len(candidates(caps, dense_70b(), TuneSpec(model="x", max_candidates=3))[1]) == 3


def test_lossy_and_experimental_need_opt_in(desktop_root: Path) -> None:
    caps = workstation_caps(desktop_root)
    with pytest.raises(TuningError, match="quality reference"):
        candidates(caps, dense_70b(), TuneSpec(model="x", allow_lossy=True))
    _, c = candidates(
        caps,
        dense_70b(),
        TuneSpec(
            model="x", allow_lossy=True, quality_reference="ref.json", include_experimental=True, max_candidates=20
        ),
    )
    by = {x["id"]: x for x in c}
    assert by["kv-q8_0"]["class"] == "lossy"
    big = by["place-large-cache"]
    assert big["experimental"] and big["param_overrides"]["threads"] == 8
    assert int(big["param_overrides"]["cpu_mask"], 16) == sum(1 << i for i in [*range(8), *range(16, 24)])


def test_adopt_then_runtime_uses_it_then_verify_reverts(
    tuner: tuple[Tuner, dict, dict, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    t, caps, ana, path = tuner
    monkeypatch.setenv("FAKE_BENCH_FAST", "-fa=on")
    rec = t.run(caps, ana, path, TuneSpec(model=path, screening_blocks=3, confirm_blocks=5, repetitions=3))
    assert rec["status"] == "adopted", rec.get("decision")
    assert rec["adopted"]["candidate"] == "fa-on" and rec["adopted"]["engine_config"] == {"flash_attn": "on"}
    assert rec["adopted"]["effect"]["verdict"] == "improvement" and rec["evidence"]["measurement_kind"] == "hardware"
    for k in ("screening_analysis", "confirm_analysis"):
        assert t.store.get(rec["evidence"][k])["schema"] == "ailx.analysis/v0"
    assert t.lookup(caps, ana)["id"] == rec["id"]
    tuned = rec["adopted"]["engine_config"]
    assert server_args({"ctx_size": 256, "parallel": 1}, tuned) == ["-c", "256", "-np", "1", "-fa", "on"]
    # later the advantage is gone (e.g. after an engine update): verification reverts to the baseline
    monkeypatch.setenv("FAKE_BENCH_FAST", "")
    rev = t.verify(caps, ana, path, rec["key"], blocks=5)
    assert rev["status"] == "reverted" and t.lookup(caps, ana) is None
    assert [h["event"] for h in rev["history"]] == ["adopted", "reverted"]


def test_no_gain_keeps_baseline(tuner: tuple[Tuner, dict, dict, str], monkeypatch: pytest.MonkeyPatch) -> None:
    t, caps, ana, path = tuner
    monkeypatch.setenv("FAKE_BENCH_FAST", "")
    rec = t.run(caps, ana, path, TuneSpec(model=path, screening_blocks=3))
    assert rec["status"] == "baseline" and rec["adopted"] is None and "screening" in rec["decision"]
    assert t.lookup(caps, ana) is None


def test_guard_regression_blocks_adoption(
    tuner: tuple[Tuner, dict, dict, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    t, caps, ana, path = tuner
    monkeypatch.setenv("FAKE_BENCH_FAST", "-fa=on")
    monkeypatch.setenv("FAKE_BENCH_PP_ONLY", "1")  # faster prefill, slower decode
    rec = t.run(caps, ana, path, TuneSpec(model=path, objective="prefill", screening_blocks=3, confirm_blocks=5))
    assert rec["status"] == "baseline"  # winner improves pp but regresses the tg guard


def test_dry_run_plans_without_measuring(tuner: tuple[Tuner, dict, dict, str]) -> None:
    t, caps, ana, path = tuner
    d = t.run(caps, ana, path, TuneSpec(model=path), dry_run=True)
    assert d["screening_campaign_spec"]["purpose"] == "screening" and d["runs"] == 3 * (1 + len(d["candidates"]))
    assert not list(t.store.root.rglob("cmp-*"))
