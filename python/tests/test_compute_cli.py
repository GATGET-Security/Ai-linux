# SPDX-License-Identifier: MIT
"""CLI wiring of ``ailx caps | model | recommend | run | tune``: JSON output, dry runs, JSON errors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_compute_model_planner import write_model
from test_compute_runtime import FAKE_SERVER
from test_compute_tuning import FAKE_BENCH

from ailx.system.cli import main


@pytest.fixture
def env(desktop_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    eng = tmp_path / "engines"
    inst = eng / "llama.cpp" / "98cc730cc8f8-cpu"
    (inst / "bin").mkdir(parents=True)
    for name, src in (("llama-server", FAKE_SERVER), ("llama-bench", FAKE_BENCH)):
        f = inst / "bin" / name
        f.write_text(src.replace("#!PYTHON", "#!" + sys.executable))
        f.chmod(0o755)
    (inst / "manifest.json").write_text(
        json.dumps({"engine": "llama.cpp", "install_id": inst.name, "variant": "cpu", "kind": "cmake"})
    )
    (eng / "llama.cpp" / "current").symlink_to(inst.name)
    models = tmp_path / "models"
    models.mkdir()
    write_model(models / "tiny.gguf")
    monkeypatch.setenv("AILX_ROOT", str(desktop_root))
    monkeypatch.setenv("AILX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AILX_ENGINES_DIR", str(eng))
    monkeypatch.setenv("AILX_MODELS_DIR", str(models))
    return tmp_path


def run_json(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    rc = main(["--json", "--no-nvml", *argv])
    return rc, json.loads(capsys.readouterr().out)


def test_caps_json(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, doc = run_json(capsys, "caps")
    assert rc == 0 and doc["schema"] == "ailx.capabilities/v0" and doc["cpu"]["physical_cores"] == 16
    assert {b["backend"]: b["usable"] for b in doc["backends"]}["cpu"] is True


def test_model_commands(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, doc = run_json(capsys, "model", "list")
    assert rc == 0 and [m["name"] for m in doc["models"]] == ["tiny.gguf"]
    rc, doc = run_json(capsys, "model", "inspect", "tiny")
    assert rc == 0 and doc["schema"] == "ailx.model-analysis/v0"
    rc, doc = run_json(capsys, "model", "plan", "tiny", "--ctx", "256")
    assert rc == 0 and doc["feasible"] and doc["mode"] == "cpu"  # the fixture GPU has no known VRAM
    rc, doc = run_json(capsys, "model", "plan", "tiny", "--kv", "q8_0")
    assert rc == 1 and "lossy" in doc["error"]


def test_recommend_run_and_tune_dry_runs(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, doc = run_json(capsys, "recommend", "tiny", "--ctx", "256")
    assert rc == 0 and doc["recommended_config"] == "engine-default" and doc["tuning"] is None
    rc, doc = run_json(capsys, "run", "start", "x", "tiny", "--ctx", "256", "--dry-run")
    assert rc == 0 and doc["status"] == "dry-run" and doc["admission"]["admitted"]
    rc, doc = run_json(capsys, "run", "list")
    assert rc == 0 and doc == []
    rc, doc = run_json(capsys, "tune", "plan", "tiny", "--n-prompt", "32", "--n-gen", "8")
    assert rc == 0 and doc["candidates"] and doc["baseline"]["id"] == "engine-default"
    rc, doc = run_json(capsys, "run", "start", "y", "tiny", "--config", "tuned", "--dry-run")
    assert rc == 1 and "no adopted tuning" in doc["error"]


def test_run_lifecycle_and_errors(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, doc = run_json(capsys, "run", "start", "svc", "tiny", "--ctx", "256", "--wait", "30")
    assert rc == 0 and doc["health"] == "ok"
    try:
        rc, doc = run_json(capsys, "run", "status", "svc")
        assert rc == 0 and doc["live"]["health"] == "ok"
    finally:
        rc, doc = run_json(capsys, "run", "stop", "svc")
    assert rc == 0 and doc["status"] == "stopped"
    rc, doc = run_json(capsys, "run", "status", "nope")
    assert rc == 1 and "no instance" in doc["error"]
    rc, doc = run_json(capsys, "model", "inspect", "missing")
    assert rc == 1 and doc["kind"] == "RuntimeError_"
