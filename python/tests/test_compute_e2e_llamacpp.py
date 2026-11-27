# SPDX-License-Identifier: MIT
"""End-to-end with real llama.cpp binaries (CI job e2e-llamacpp): managed llama-server and a short tuning run.

Runs on the CI runner's CPU with the random-weight test model: it validates the mechanics (process
management, HTTP serving, campaigns, adoption logic), not the performance of any configuration.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from ailx.compute.capabilities import collect_capabilities
from ailx.compute.model import analyze
from ailx.compute.runtime import RuntimeManager, StartSpec
from ailx.compute.tuning import Tuner, TuneSpec
from ailx.store import Store
from ailx.system.engines import EngineError, EngineManager
from ailx.system.paths import load_layout

pytestmark = pytest.mark.llamacpp


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    model = os.environ.get("AILX_TINY_MODEL")
    if not model or not Path(model).is_file():
        pytest.skip("AILX_TINY_MODEL not set")
    monkeypatch.setenv("AILX_STATE_DIR", str(tmp_path / "state"))
    lay = load_layout()
    try:
        EngineManager(lay).binary("llama.cpp", "llama-server")
    except EngineError as exc:
        pytest.skip(f"no engine-managed llama-server: {exc}")
    return lay, collect_capabilities(lay, use_nvml=False), model


def test_managed_llama_server(setup: tuple) -> None:
    lay, caps, model = setup
    rm = RuntimeManager(lay)
    rec = rm.start(StartSpec(name="e2e", model=model, ctx=256, config="planned", threads=2), caps)
    try:
        assert rm.wait_ready("e2e", 120) == "ok", rm.logs("e2e")
        body = json.dumps({"prompt": "the a of", "n_predict": 8}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{rec['port']}/completion", data=body, headers={"Content-Type": "application/json"}
        )
        out = json.loads(urllib.request.urlopen(req, timeout=120).read())
        assert out["tokens_predicted"] == 8
        st = rm.status("e2e")
        assert st["live"]["rss_bytes"] > 0 and st["plan"]["mode"] == "cpu"
    finally:
        assert rm.stop("e2e")["status"] == "stopped"


def test_tuning_pipeline_on_real_llama_bench(setup: tuple, tmp_path: Path) -> None:
    lay, caps, model = setup
    t = Tuner(lay, Store(tmp_path / "store"), log=lambda *a: None)
    spec = TuneSpec(model=model, n_prompt=32, n_gen=16, repetitions=2, screening_blocks=2, confirm_blocks=3)
    rec = t.run(caps, analyze(model), model, spec)
    # either outcome is legitimate on a shared CI runner; what matters is a complete, evidenced decision
    assert rec["status"] in ("adopted", "baseline") and rec["evidence"]["measurement_kind"] == "hardware"
    assert t.store.get(rec["evidence"]["screening_analysis"])["schema"] == "ailx.analysis/v0"
