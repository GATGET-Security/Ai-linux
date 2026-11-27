# SPDX-License-Identifier: MIT
"""Engine management: pinned versions, isolated installations, integrity, reproducible build manifests.

* ``cmake`` engines (llama.cpp) are fetched at a pinned commit, built in a staging directory with
  static linking (relocatable), and atomically renamed into place.
* ``pip`` engines (vLLM) get their own virtual environment at their final path (venvs are not
  relocatable); an ``.incomplete`` marker protects against half-finished installs.
* Every installation carries ``manifest.json`` (``engine-manifest.v0``) with source, flags,
  toolchain versions and sha256 of every artifact. Its canonical sha256 is the runtime-bundle
  digest recorded in measurement fingerprints.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from ailx.core import Doc, canonical_bytes, file_sha256, sha256_hex, utcnow, validate
from ailx.system.paths import Layout

Log = Callable[[str], None]


class EngineError(RuntimeError):
    pass


def catalog() -> dict[str, Doc]:
    path = os.environ.get("AILX_ENGINE_CATALOG")
    text = Path(path).read_text() if path else resources.files("ailx.system").joinpath("data/engines.json").read_text()
    data: dict[str, Doc] = json.loads(text)
    return data


def manifest_digest(manifest: Doc) -> str:
    return sha256_hex(canonical_bytes(manifest))


def _tool_version(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return next((ln for ln in out if ln.strip()), None)


def _run(cmd: list[str], log: Log, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    log("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise EngineError(f"command failed ({r.returncode}): {' '.join(cmd)}\n{(r.stdout + r.stderr)[-3000:]}")


def _artifacts(root: Path, files: list[Path]) -> list[Doc]:
    return [
        {"path": str(f.relative_to(root)), "sha256": file_sha256(f), "size": f.stat().st_size} for f in sorted(files)
    ]


class EngineManager:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.base = layout.engines_dir

    # -- queries ---------------------------------------------------------------------------
    def spec(self, engine: str) -> Doc:
        cat = catalog()
        if engine not in cat:
            raise EngineError(f"unknown engine {engine!r}; known: {', '.join(sorted(cat))}")
        return cat[engine]

    def installs(self, engine: str | None = None) -> list[Doc]:
        out = []
        engines = [engine] if engine else sorted(p.name for p in self.base.glob("*") if p.is_dir())
        for e in engines:
            for d in sorted((self.base / e).glob("*")) if (self.base / e).is_dir() else []:
                if d.name.startswith(".") or d.is_symlink() or not d.is_dir():
                    continue
                mf = d / "manifest.json"
                if mf.is_file() and not (d / ".incomplete").exists():
                    out.append(json.loads(mf.read_text()))
                else:
                    out.append({"engine": e, "install_id": d.name, "broken": True})
        return out

    def install_dir(self, engine: str, install_id: str) -> Path:
        return self.base / engine / install_id

    def current(self, engine: str) -> Doc | None:
        link = self.base / engine / "current"
        if not link.is_symlink():
            return None
        mf = link.resolve() / "manifest.json"
        if not mf.is_file():
            return None
        doc: Doc = json.loads(mf.read_text())
        return doc

    def use(self, engine: str, install_id: str) -> None:
        d = self.install_dir(engine, install_id)
        if not (d / "manifest.json").is_file():
            raise EngineError(f"{engine}/{install_id} is not a complete installation")
        link = self.base / engine / "current"
        tmp = link.with_name(".current.tmp")
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(install_id)
        os.replace(tmp, link)

    def binary(self, engine: str, name: str) -> tuple[Path, Doc]:
        m = self.current(engine)
        if not m:
            raise EngineError(f"no current installation of {engine}; run 'ailx engine install {engine}'")
        d = self.install_dir(engine, m["install_id"])
        p = d / ("venv/bin" if m["kind"] == "pip" else "bin") / name
        if not p.exists():
            raise EngineError(f"{name} not part of {engine}/{m['install_id']}")
        return p, m

    # -- install ---------------------------------------------------------------------------
    def install(
        self,
        engine: str,
        variant: str,
        source: str | None = None,
        jobs: int | None = None,
        targets: list[str] | None = None,
        log: Log = print,
        make_current: bool = True,
    ) -> Doc:
        spec = self.spec(engine)
        if variant not in spec["variants"]:
            raise EngineError(f"{engine}: unknown variant {variant!r}; known: {', '.join(spec['variants'])}")
        install_id = f"{(spec.get('ref') or spec['version'])[:12]}-{variant}"
        if targets:
            install_id += "-custom"
        dest = self.install_dir(engine, install_id)
        if (dest / "manifest.json").is_file() and not (dest / ".incomplete").exists():
            if self.verify(engine, install_id)["ok"]:
                log(f"{engine}/{install_id} already installed and intact")
                if make_current:
                    self.use(engine, install_id)
                return self._manifest(engine, install_id)
            raise EngineError(f"{engine}/{install_id} exists but fails verification; remove it first")
        (self.base / engine).mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        if spec["kind"] == "cmake":
            manifest = self._install_cmake(engine, spec, variant, install_id, source, jobs, targets, log)
        elif spec["kind"] == "pip":
            manifest = self._install_pip(engine, spec, variant, install_id, log)
        else:
            raise EngineError(f"unsupported engine kind {spec['kind']}")
        log(f"installed {engine}/{install_id} in {time.time() - t0:.0f} s")
        if make_current:
            self.use(engine, install_id)
        return manifest

    def _base_manifest(self, engine: str, spec: Doc, variant: str, install_id: str) -> Doc:
        return {
            "schema": "ailx.engine-manifest/v0",
            "engine": engine,
            "install_id": install_id,
            "kind": spec["kind"],
            "version": spec.get("ref") or spec["version"],
            "variant": variant,
            "licence": spec.get("licence"),
            "host": {"machine": platform.machine(), "kernel": platform.release(), "python": platform.python_version()},
            "notes": [],
        }

    def _install_cmake(
        self,
        engine: str,
        spec: Doc,
        variant: str,
        install_id: str,
        source: str | None,
        jobs: int | None,
        targets: list[str] | None,
        log: Log,
    ) -> Doc:
        stage = self.base / engine / f".staging-{install_id}-{os.getpid()}"
        shutil.rmtree(stage, ignore_errors=True)
        src, build, dist = stage / "src", stage / "build", stage / "bin"
        try:
            src.mkdir(parents=True)
            url = source or spec["source"]
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
            _run(["git", "init", "-q", str(src)], log)
            _run(["git", "-C", str(src), "fetch", "-q", "--depth", "1", url, spec["ref"]], log, env=env)
            _run(["git", "-C", str(src), "checkout", "-q", "--detach", "FETCH_HEAD"], log)
            head = subprocess.run(
                ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
            if re.fullmatch(r"[0-9a-f]{40}", spec["ref"]) and head != spec["ref"]:
                raise EngineError(f"fetched commit {head} does not match pinned {spec['ref']}")
            flags = [*spec["common_flags"], *spec["variants"][variant]]
            tgts = targets or spec["targets"]
            _run(["cmake", "-S", str(src), "-B", str(build), *flags], log)
            _run(["cmake", "--build", str(build), "-j", str(jobs or os.cpu_count() or 2), "--target", *tgts], log)
            dist.mkdir()
            files = []
            for t in tgts:
                exe = next((c for c in (build / "bin" / t, build / t) if c.is_file()), build / "bin" / t)
                if not exe.is_file():
                    raise EngineError(f"build did not produce {t}")
                shutil.copy2(exe, dist / t)
                files.append(dist / t)
            smoke = spec.get("smoke")
            smoke_out = None
            if smoke and (dist / smoke[0]).exists():
                r = subprocess.run([str(dist / smoke[0]), *smoke[1:]], capture_output=True, text=True, timeout=60)
                smoke_out = (r.stdout + r.stderr).strip().splitlines()[:2]
                if r.returncode != 0:
                    raise EngineError(f"smoke test failed: {smoke_out}")
            m = self._base_manifest(engine, spec, variant, install_id)
            m.update(
                {
                    "source": {"url": url, "pinned_ref": spec["ref"], "commit": head},
                    "build": {
                        "system": "cmake",
                        "flags": flags,
                        "targets": tgts,
                        "static": "-DBUILD_SHARED_LIBS=OFF" in flags,
                        "smoke": smoke_out,
                        "started": utcnow(),
                    },
                    "toolchain": {
                        "cc": _tool_version(["cc", "--version"]),
                        "cxx": _tool_version(["c++", "--version"]),
                        "cmake": _tool_version(["cmake", "--version"]),
                        "git": _tool_version(["git", "--version"]),
                        "nvcc": _tool_version(["nvcc", "--version"]) if "CUDA" in " ".join(flags) else None,
                    },
                    "artifacts": _artifacts(stage, files),
                    "installed_at": utcnow(),
                }
            )
            m["notes"].append(
                "binaries are not bit-reproducible across toolchains; source commit, flags and "
                "toolchain versions identify the build"
            )
            if source:
                m["notes"].append(f"source overridden: {source}")
            validate(m, "ailx.engine-manifest/v0")
            shutil.rmtree(src)
            shutil.rmtree(build)
            (stage / "manifest.json").write_text(json.dumps(m, indent=2))
            os.replace(stage, self.install_dir(engine, install_id))
            return m
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _install_pip(self, engine: str, spec: Doc, variant: str, install_id: str, log: Log) -> Doc:
        dest = self.install_dir(engine, install_id)
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        (dest / ".incomplete").write_text("installation in progress\n")
        try:
            venv = dest / "venv"
            _run([sys.executable, "-m", "venv", str(venv)], log)
            py = venv / "bin" / "python"
            req = f"{spec['package']}=={spec['version']}"
            _run(
                [
                    str(py),
                    "-m",
                    "pip",
                    "install",
                    "--no-input",
                    "--disable-pip-version-check",
                    *spec["variants"][variant],
                    req,
                ],
                log,
            )
            freeze = subprocess.run(
                [str(py), "-m", "pip", "freeze", "--all"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            records = sorted(venv.glob("lib/python*/site-packages/*.dist-info/RECORD"))
            smoke = spec.get("smoke")
            smoke_out = None
            if smoke:
                exe = venv / "bin" / smoke[0]
                r = subprocess.run([str(exe), *smoke[1:]], capture_output=True, text=True, timeout=300)
                smoke_out = (r.stdout + r.stderr).strip().splitlines()[:2]
                if r.returncode != 0:
                    raise EngineError(f"smoke test failed: {smoke_out}")
            m = self._base_manifest(engine, spec, variant, install_id)
            m.update(
                {
                    "source": {"index": "PyPI (pip default index)", "requirement": req},
                    "build": {
                        "system": "pip",
                        "pip_args": spec["variants"][variant],
                        "freeze": freeze,
                        "smoke": smoke_out,
                    },
                    "toolchain": {
                        "python": sys.version.split()[0],
                        "pip": _tool_version([str(py), "-m", "pip", "--version"]),
                    },
                    "artifacts": _artifacts(dest, records),
                    "installed_at": utcnow(),
                }
            )
            m["notes"].append(
                "artifacts are the dist-info RECORD files; 'verify --full' re-hashes every installed file"
            )
            validate(m, "ailx.engine-manifest/v0")
            (dest / "manifest.json").write_text(json.dumps(m, indent=2))
            (dest / ".incomplete").unlink()
            return m
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    # -- integrity ---------------------------------------------------------------------------
    def _manifest(self, engine: str, install_id: str) -> Doc:
        doc: Doc = json.loads((self.install_dir(engine, install_id) / "manifest.json").read_text())
        return doc

    def verify(self, engine: str, install_id: str | None = None, full: bool = False) -> dict[str, Any]:
        cur = self.current(engine)
        iid = install_id or (cur["install_id"] if cur else None)
        if not iid:
            raise EngineError(f"no installation of {engine} selected")
        d = self.install_dir(engine, iid)
        if (d / ".incomplete").exists() or not (d / "manifest.json").is_file():
            return {"engine": engine, "install_id": iid, "ok": False, "problems": ["incomplete installation"]}
        m = self._manifest(engine, iid)
        problems = []
        for a in m["artifacts"]:
            p = d / a["path"]
            if not p.is_file():
                problems.append(f"missing {a['path']}")
            elif file_sha256(p) != a["sha256"]:
                problems.append(f"modified {a['path']}")
        if full and m["kind"] == "pip":
            problems += _verify_records(d / "venv", [d / a["path"] for a in m["artifacts"]])
        return {
            "engine": engine,
            "install_id": iid,
            "ok": not problems,
            "problems": problems,
            "manifest_sha256": manifest_digest(m),
            "full": full,
        }

    def remove(self, engine: str, install_id: str) -> None:
        d = self.install_dir(engine, install_id)
        if not d.exists():
            raise EngineError(f"{engine}/{install_id} not installed")
        link = self.base / engine / "current"
        if link.is_symlink() and os.readlink(link) == install_id:
            link.unlink()
        shutil.rmtree(d)

    def env(self) -> dict[str, str]:
        """Shell variables for the measurement harness (workloads use $AILX_LLAMA_BENCH etc.)."""
        out: dict[str, str] = {}
        for name, var in (
            ("llama-bench", "AILX_LLAMA_BENCH"),
            ("llama-perplexity", "AILX_LLAMA_PERPLEXITY"),
            ("llama-server", "AILX_LLAMA_SERVER"),
        ):
            with contextlib.suppress(EngineError):
                out[var] = str(self.binary("llama.cpp", name)[0])
        with contextlib.suppress(EngineError):
            out["AILX_VLLM"] = str(self.binary("vllm", "vllm")[0])
        return out


def _verify_records(venv: Path, records: list[Path]) -> list[str]:
    problems = []
    site = records[0].parent.parent if records else venv
    for rec in records:
        with open(rec, newline="") as f:
            for row in csv.reader(f):
                if len(row) < 2 or not row[1].startswith("sha256="):
                    continue
                p = (site / row[0]).resolve()
                if not p.is_file():
                    problems.append(f"missing {row[0]}")
                    continue
                digest = base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).rstrip(b"=").decode()
                if digest != row[1][7:]:
                    problems.append(f"modified {row[0]}")
    return problems


def resolve_engine_ref(ref: str, layout: Layout) -> tuple[str, Doc]:
    """``engine:<engine>/<binary>`` → (absolute path, manifest)."""
    engine, _, name = ref[len("engine:") :].partition("/")
    path, m = EngineManager(layout).binary(engine, name)
    return str(path), m
