#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the native ``ailx`` Debian package (architecture ``all``) reproducibly.

Usage: python3 packaging/deb/build_deb.py [--version 0.1.0-1] [--out dist/]

* Python code goes to /usr/lib/python3/dist-packages/ailx (root-owned), entry points use
  ``/usr/bin/python3 -I`` (isolated mode: no PYTHONPATH, no user site, no script dir on sys.path).
* Dependencies are Ubuntu packages (security updates via apt), nothing is vendored.
* Privileged helpers live in /usr/libexec/ailx; the sudoers rule is NOT shipped (opt-in via
  ``ailx system sudoers enable``).
* Reproducible: fixed file order/ownership (dpkg-deb --root-owner-group), mtimes clamped to
  SOURCE_DATE_EPOCH (default: last git commit time), no __pycache__ in the package.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

DEPENDS = [
    "python3 (>= 3.12)",
    "python3-numpy",
    "python3-scipy",
    "python3-jsonschema",
    "python3-zstandard",
    "python3-rfc8785",
    "systemd",
    "sudo",
]
RECOMMENDS = ["git", "cmake", "build-essential", "python3-gguf"]
SUGGESTS = ["nvidia-utils", "python3-venv"]


def epoch() -> int:
    if "SOURCE_DATE_EPOCH" in os.environ:
        return int(os.environ["SOURCE_DATE_EPOCH"])
    out = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%ct"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def put(root: Path, rel: str, data: bytes, mode: int = 0o644) -> None:
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    p.chmod(mode)


def isolated(src: Path) -> bytes:
    lines = src.read_text().splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines[0] = "#!/usr/bin/python3 -I\n"
    return "".join(lines).encode()


def build(version: str, out: Path) -> Path:
    t = epoch()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        # python package (+ canonical schemas and helper sources used by the code at runtime)
        dst = root / "usr/lib/python3/dist-packages/ailx"
        shutil.copytree(REPO / "python/src/ailx", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(REPO / "schemas", dst / "_schemas", ignore=shutil.ignore_patterns("fixtures"))
        (dst / "_package_version").write_text(version + "\n")
        shutil.copytree(REPO / "helpers", dst / "_helpers", ignore=shutil.ignore_patterns("__pycache__"))
        for name, mod in (("ailx", "ailx.system.cli"), ("ailx-bench", "ailx.cli")):
            put(
                root,
                f"/usr/bin/{name}",
                f"#!/usr/bin/python3 -I\n# SPDX-License-Identifier: MIT\n"
                f"from {mod} import main\nraise SystemExit(main())\n".encode(),
                0o755,
            )
        put(
            root,
            "/usr/libexec/ailx/ailx-apply-helper",
            isolated(REPO / "helpers/apply-helper/ailx_apply_helper.py"),
            0o755,
        )
        put(
            root,
            "/usr/libexec/ailx/ailx-rapl-collector",
            isolated(REPO / "helpers/rapl-collector/ailx_rapl_collector.py"),
            0o755,
        )
        unit = (REPO / "helpers/rapl-collector/ailx-rapl-collector.service").read_text()
        unit = unit.replace(
            "/usr/bin/python3 /usr/local/libexec/ailx_rapl_collector.py", "/usr/libexec/ailx/ailx-rapl-collector"
        )
        put(root, "/usr/lib/systemd/system/ailx-rapl-collector.service", unit.encode())
        for rel, src in (
            ("usr/lib/sysusers.d/ailx.conf", "ailx.sysusers"),
            ("usr/lib/tmpfiles.d/ailx.conf", "ailx.tmpfiles"),
            ("etc/ailx/ailx.toml", "ailx.toml"),
        ):
            put(root, rel, (HERE / "files" / src).read_bytes())
        put(root, "/usr/share/ailx/packaged", f"ailx {version}\n".encode())
        for page in ("ailx.1", "ailx-bench.1"):
            put(root, f"/usr/share/man/man1/{page}.gz", gzip.compress((HERE / "man" / page).read_bytes(), 9, mtime=0))
        shutil.copytree(REPO / "workloads", root / "usr/share/ailx/workloads")
        put(root, "/usr/share/doc/ailx/copyright", (HERE / "copyright").read_bytes())
        changelog = (HERE / "changelog").read_text().replace("@VERSION@", version)
        put(
            root, "/usr/share/doc/ailx/changelog.Debian.gz", gzip.compress(changelog.encode(), compresslevel=9, mtime=0)
        )
        # control
        size_kb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) // 1024 + 1
        control = (
            f"Package: ailx\nVersion: {version}\nArchitecture: all\nMaintainer: AI Linux contributors "
            f"<maintainers@ai-linux.invalid>\nInstalled-Size: {size_kb}\nSection: admin\nPriority: optional\n"
            f"Depends: {', '.join(DEPENDS)}\nRecommends: {', '.join(RECOMMENDS)}\nSuggests: {', '.join(SUGGESTS)}\n"
            "Homepage: https://github.com/GATGET-Security/Ai-linux\n"
            "Description: AI Linux system layer and measurement harness\n"
            " ailx: hardware inventory, diagnosis, declarative system profiles with\n"
            " journaled transactions and rollback, engine management (llama.cpp, vLLM).\n"
            " ailx-bench: reproducible benchmark campaigns with statistics and quality\n"
            " gates. Profile settings are experimental and inactive by default until\n"
            " hardware-validated.\n"
        )
        put(root, "/DEBIAN/control", control.encode())
        put(root, "/DEBIAN/conffiles", b"/etc/ailx/ailx.toml\n")
        for script in ("postinst", "prerm", "postrm"):
            put(root, f"/DEBIAN/{script}", (HERE / script).read_bytes(), 0o755)
        md5 = []
        for f in sorted(p for p in root.rglob("*") if p.is_file() and "DEBIAN" not in p.parts):
            md5.append(f"{hashlib.md5(f.read_bytes()).hexdigest()}  {f.relative_to(root)}")
        put(root, "/DEBIAN/md5sums", ("\n".join(md5) + "\n").encode())
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_dir():
                p.chmod(0o755)
            os.utime(p, (t, t), follow_symlinks=False)
        out.mkdir(parents=True, exist_ok=True)
        deb = out / f"ailx_{version}_all.deb"
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(t))
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "-z9", "--build", str(root), str(deb)],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
    return deb


def main() -> None:
    ap = argparse.ArgumentParser()
    py = tomllib.loads((REPO / "python/pyproject.toml").read_text())["project"]["version"]
    ap.add_argument("--version", default=f"{py}-1")
    ap.add_argument("--out", default=str(REPO / "dist"))
    a = ap.parse_args()
    deb = build(a.version, Path(a.out))
    print(f"{deb}  sha256={hashlib.sha256(deb.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
