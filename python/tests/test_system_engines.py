# SPDX-License-Identifier: MIT
"""Engine management with real builds: a tiny local CMake project and a real pip package."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ailx.system.engines import EngineError, EngineManager, manifest_digest
from ailx.system.paths import load_layout
from ailx.workloads import bundle, resolve


@pytest.fixture
def tiny_project(tmp_path: Path) -> tuple[Path, str]:
    src = tmp_path / "tinyengine"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(tiny C)\nadd_executable(tiny-bench main.c)\n"
    )
    (src / "main.c").write_text('#include <stdio.h>\nint main(int c,char**v){puts("tiny-bench 1.0");return 0;}\n')
    env = dict(
        os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e"
    )
    for cmd in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=src, check=True, env=env)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    return src, sha


@pytest.fixture
def catalog(tmp_path: Path, tiny_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> Path:
    src, sha = tiny_project
    cat = {
        "tiny": {
            "kind": "cmake",
            "licence": "MIT",
            "source": str(src),
            "ref": sha,
            "common_flags": ["-DCMAKE_BUILD_TYPE=Release"],
            "variants": {"cpu": []},
            "targets": ["tiny-bench"],
            "smoke": ["tiny-bench"],
        },
        "pypkg": {
            "kind": "pip",
            "licence": "MIT",
            "package": "six",
            "version": "1.17.0",
            "variants": {"cpu": []},
            "smoke": ["python", "-c", "import six; print(six.__version__)"],
        },
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(cat))
    monkeypatch.setenv("AILX_ENGINE_CATALOG", str(p))
    return p


def test_cmake_engine_install_verify_tamper_remove(host: Path, catalog: Path) -> None:
    em = EngineManager(load_layout())
    m = em.install("tiny", "cpu", log=lambda s: None)
    assert m["source"]["commit"] == m["source"]["pinned_ref"] and m["build"]["smoke"] == ["tiny-bench 1.0"]
    assert [a["path"] for a in m["artifacts"]] == ["bin/tiny-bench"]
    assert m["toolchain"]["cmake"] and m["toolchain"]["cc"]
    d = em.install_dir("tiny", m["install_id"])
    assert not (d / "src").exists() and not (d / "build").exists()  # build tree removed
    assert not list(d.parent.glob(".staging-*"))
    assert em.verify("tiny")["ok"]
    assert em.install("tiny", "cpu", log=lambda s: None)["install_id"] == m["install_id"]  # idempotent
    (d / "bin/tiny-bench").write_bytes(b"tampered")
    v = em.verify("tiny")
    assert not v["ok"] and v["problems"] == ["modified bin/tiny-bench"]
    with pytest.raises(EngineError, match="fails verification"):
        em.install("tiny", "cpu", log=lambda s: None)
    em.remove("tiny", m["install_id"])
    assert em.installs("tiny") == [] and em.current("tiny") is None


def test_engine_reference_resolves_for_workloads(host: Path, catalog: Path) -> None:
    em = EngineManager(load_layout())
    m = em.install("tiny", "cpu", log=lambda s: None)
    path = resolve("engine:tiny/tiny-bench")
    assert subprocess.run([path], capture_output=True, text=True).stdout.strip() == "tiny-bench 1.0"
    b = bundle("engine:tiny/tiny-bench")
    assert b["digest"] == manifest_digest(m) and b["path"] == path


def test_failed_build_leaves_nothing(host: Path, catalog: Path, tiny_project: tuple[Path, str]) -> None:
    src, _ = tiny_project
    (src / "main.c").write_text("this does not compile")
    subprocess.run(
        ["git", "commit", "-qam", "break"],
        cwd=src,
        check=True,
        env=dict(
            os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e"
        ),
    )
    cat = json.loads(catalog.read_text())
    cat["tiny"]["ref"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True
    ).stdout.strip()
    catalog.write_text(json.dumps(cat))
    em = EngineManager(load_layout())
    with pytest.raises(EngineError, match="command failed"):
        em.install("tiny", "cpu", log=lambda s: None)
    assert not any((em.base / "tiny").iterdir())


def test_pinned_ref_mismatch_is_rejected(host: Path, catalog: Path) -> None:
    cat = json.loads(catalog.read_text())
    cat["tiny"]["ref"] = "0" * 40
    catalog.write_text(json.dumps(cat))
    with pytest.raises(EngineError):
        EngineManager(load_layout()).install("tiny", "cpu", log=lambda s: None)


@pytest.mark.slow
def test_pip_engine_isolated_venv_full_verify(host: Path, catalog: Path) -> None:
    """Real pip install from PyPI (stand-in package for the vLLM code path; needs network)."""
    em = EngineManager(load_layout())
    try:
        m = em.install("pypkg", "cpu", log=lambda s: None)
    except EngineError as exc:
        if "Could not" in str(exc) or "network" in str(exc).lower():
            pytest.skip(f"PyPI not reachable: {exc}")
        raise
    assert m["source"]["requirement"] == "six==1.17.0" and "six==1.17.0" in m["build"]["freeze"]
    assert m["build"]["smoke"] == ["1.17.0"]
    v = em.verify("pypkg", full=True)
    assert v["ok"], v["problems"]
    six_py = next((em.install_dir("pypkg", m["install_id"]) / "venv").glob("lib/python*/site-packages/six.py"))
    six_py.write_text(six_py.read_text() + "\n# tampered\n")
    assert em.verify("pypkg")["ok"]  # quick verify checks RECORD files only
    full = em.verify("pypkg", full=True)
    assert not full["ok"] and any("six.py" in p for p in full["problems"])
