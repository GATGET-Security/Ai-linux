# SPDX-License-Identifier: MIT
"""End-to-end through the CLI: synthetic A/A calibration, 5% effect, compute smoke run, tiny llama-bench."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ailx.cli import main
from ailx.report import render, validate_claims
from ailx.store import Store

REPO = Path(__file__).resolve().parents[2]


def _campaign(tmp_path: Path, name: str, **changes: object) -> Path:
    spec = json.loads((REPO / "workloads/campaigns" / name).read_text())
    spec["workload"] = str((REPO / "workloads/campaigns" / spec["workload"]).resolve())
    spec.update(changes)
    p = tmp_path / name
    p.write_text(json.dumps(spec))
    return p


@pytest.mark.slow
def test_synthetic_aa_then_effect(tmp_path: Path) -> None:
    store = tmp_path / "store"
    assert (
        main(["--store", str(store), "--no-nvml", "campaign", "all", str(_campaign(tmp_path, "synthetic-aa.json"))])
        == 0
    )
    st = Store(store)
    cal = st.list_docs("ailx.analysis/v0")[-1][0]
    aa = st.get(cal)
    assert aa["measurement_kind"] == "simulated"
    assert all(c["verdict"] not in ("improvement", "regression") for c in aa["comparisons"])
    assert aa["calibration"]["noise_floor"]["work_rate"] < 0.02
    assert (
        main(
            [
                "--store",
                str(store),
                "--no-nvml",
                "campaign",
                "all",
                "--calibration",
                cal,
                str(_campaign(tmp_path, "synthetic-effect-5pct.json")),
            ]
        )
        == 0
    )
    ana = st.get(st.list_docs("ailx.analysis/v0")[-1][0])
    primary = next(c for c in ana["comparisons"] if c["role"] == "primary")
    assert primary["verdict"] == "improvement" and primary["calibrated"] is True
    text = render(st, ana)
    assert "SIMULATED" in text and validate_claims(text) == []
    assert st.verify() == []


def test_compute_mode_is_hardware_kind(tmp_path: Path) -> None:
    store = tmp_path / "store"
    assert main(["--store", str(store), "--no-nvml", "run", str(REPO / "workloads/smoke/synthetic-compute.json")]) == 0
    st = Store(store)
    res = st.get(st.list_docs("ailx.result/v0")[0][0])
    assert res["measurement_kind"] == "hardware" and res["metrics"]["work_rate"]["value"] > 0
    assert res["telemetry"]["psi"]["available"] in (True, False)


def test_failed_run_is_recorded_not_hidden(tmp_path: Path) -> None:
    wl = json.loads((REPO / "workloads/smoke/llama-bench-tiny.json").read_text())
    wl["runtime"]["binary"] = "/nonexistent/llama-bench"
    wl["model"]["path"] = str(tmp_path / "missing.gguf")
    p = tmp_path / "wl.json"
    p.write_text(json.dumps(wl))
    assert main(["--store", str(tmp_path / "s"), "--no-nvml", "run", str(p)]) == 1
    st = Store(tmp_path / "s")
    res = st.get(st.list_docs("ailx.result/v0")[0][0])
    assert res["status"] == "failed" and res["metrics"] == {}


@pytest.mark.llamacpp
@pytest.mark.skipif(
    not (os.environ.get("AILX_LLAMA_BENCH") and os.environ.get("AILX_TINY_MODEL")),
    reason="set AILX_LLAMA_BENCH and AILX_TINY_MODEL",
)
def test_tiny_model_campaign(tmp_path: Path) -> None:
    store = tmp_path / "store"
    assert (
        main(
            [
                "--store",
                str(store),
                "--no-nvml",
                "campaign",
                "all",
                str(_campaign(tmp_path, "llama-bench-tiny-aa.json")),
            ]
        )
        == 0
    )
    st = Store(store)
    ana = st.get(st.list_docs("ailx.analysis/v0")[-1][0])
    assert ana["measurement_kind"] == "hardware" and len(ana["result_ids"]) == 6
    res = st.get(ana["result_ids"][0])
    assert res["tool"]["name"] == "llama-bench" and "tg_tokens_per_s" in res["metrics"]
    assert res["telemetry"]["nvml"]["available"] is False  # no GPU in CI: reported, not faked
