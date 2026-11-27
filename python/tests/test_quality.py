# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from ailx.quality.kld import evaluate, gate, make_reference, parse_kld_output

FIX = Path(__file__).parent / "fixtures"


def test_parse_captured_llama_perplexity_output() -> None:
    m = parse_kld_output((FIX / "llama-perplexity-kld-98cc730.txt").read_text())
    assert m["kld_mean"]["value"] == pytest.approx(0.011109) and m["kld_mean"]["se"] == pytest.approx(0.000132)
    assert m["same_top_p"]["value"] == pytest.approx(47.817)
    assert {"ppl_q", "ppl_base", "kld_p99", "kld_max", "rms_dp"} <= set(m)


def test_gate_rule() -> None:
    aa = {"kld_mean": {"value": 0.0, "se": 0.0}, "same_top_p": {"value": 100.0, "se": 0.0}}
    same = {"kld_mean": {"value": 0.0, "se": 0.0}, "same_top_p": {"value": 100.0, "se": 0.0}}
    lossy = {"kld_mean": {"value": 0.01, "se": 0.0001}, "same_top_p": {"value": 95.0, "se": 0.5}}
    assert gate(same, aa)[0] == "no-detectable-quality-change"
    assert gate(lossy, aa)[0] == "quality-change-detected"


LLAMA_BIN = os.environ.get("AILX_LLAMA_CPP_BIN")
TINY = os.environ.get("AILX_TINY_DIR")


@pytest.mark.llamacpp
@pytest.mark.skipif(not (LLAMA_BIN and TINY), reason="set AILX_LLAMA_CPP_BIN and AILX_TINY_DIR (see CI e2e job)")
def test_end_to_end_gate_with_tiny_model(tmp_path: Path) -> None:
    ppl = str(Path(LLAMA_BIN) / "llama-perplexity")  # type: ignore[arg-type]
    tiny = Path(TINY)  # type: ignore[arg-type]
    meta = make_reference(ppl, str(tiny / "tiny-f16.gguf"), str(tiny / "corpus.txt"), tmp_path / "ref", 128, 8, 2)
    assert meta["aa"]["kld_mean"]["value"] <= 1e-4
    ref = tmp_path / "ref" / "reference.json"
    assert evaluate(ppl, str(tiny / "tiny-q4_0.gguf"), ref, 2)["verdict"] == "quality-change-detected"
    assert evaluate(ppl, str(tiny / "tiny-f16.gguf"), ref, 2)["verdict"] == "no-detectable-quality-change"
    other = tmp_path / "other.txt"
    shutil.copy(tiny / "corpus.txt", other)
    other.write_text(other.read_text() + " extra")
    r = evaluate(ppl, str(tiny / "tiny-f16.gguf"), ref, 2, corpus=str(other))
    assert r["verdict"] == "prerequisite-failed" and "corpus-identical" in r["detail"]
