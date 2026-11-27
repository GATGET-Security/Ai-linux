# SPDX-License-Identifier: MIT
"""Install / uninstall the AI Linux system layer on the host distribution (layer mode, ADR-0005).

What is installed (all recorded in ``<state_dir>/install-manifest.json``):

* group ``ailx`` (operators allowed to use the privileged helper and the RAPL collector),
* directories ``/etc/ailx``, ``/var/lib/ailx`` (setgid, group-writable), ``/opt/ailx/engines``,
* ``/usr/local/libexec/ailx-apply-helper`` and ``/usr/local/libexec/ailx_rapl_collector.py``
  (shebang ``/usr/bin/python3 -I`` — isolated mode, no PYTHONPATH/user-site),
* ``/etc/systemd/system/ailx-rapl-collector.service`` (installed, **not** enabled),
* ``/etc/ailx/ailx.toml`` (paths), optionally ``/etc/sudoers.d/ailx`` (``--with-sudoers``, validated
  with ``visudo -c``) and ``/usr/local/bin/ailx`` / ``ailx-bench`` symlinks to the running venv.

Nothing that already exists with different content is overwritten (unless ``--force``, which keeps a
backup). Uninstall removes only what the manifest recorded and only if unmodified; ``--purge``
also removes state, engines and the group. No kernel parameter, boot entry or security setting is
touched.
"""

from __future__ import annotations

import grp
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ailx.core import Doc, sha256_hex, utcnow
from ailx.system.paths import Layout

HELPER = "/usr/local/libexec/ailx-apply-helper"
RAPL = "/usr/local/libexec/ailx_rapl_collector.py"
RAPL_UNIT = "/etc/systemd/system/ailx-rapl-collector.service"
SUDOERS = "/etc/sudoers.d/ailx"
GROUP = "ailx"


class InstallError(RuntimeError):
    pass


@dataclass
class Action:
    kind: str  # group | dir | file | symlink | exec
    path: str
    mode: int | None = None
    group: str | None = None
    content: bytes | None = None
    target: str | None = None
    note: str = ""

    def describe(self) -> str:
        m = f" mode {oct(self.mode)}" if self.mode is not None else ""
        g = f" group {self.group}" if self.group else ""
        t = f" -> {self.target}" if self.target else ""
        return f"{self.kind:<7} {self.path}{t}{m}{g}{('  # ' + self.note) if self.note else ''}"


def helpers_dir() -> Path:
    here = Path(__file__).resolve()
    if (here.parents[1] / "_helpers").is_dir():
        return here.parents[1] / "_helpers"
    for p in here.parents:
        if (p / "helpers" / "apply-helper").is_dir():
            return p / "helpers"
    raise InstallError("helper sources not found")


def _with_isolated_shebang(src: Path) -> bytes:
    lines = src.read_text().splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines[0] = "#!/usr/bin/python3 -I\n"
    return "".join(lines).encode()


def _rel(layout: Layout, p: Path) -> str:
    """Host-absolute form of ``p`` (strip a test root prefix)."""
    try:
        return "/" + str(p.relative_to(layout.root)) if str(layout.root) != "/" else str(p)
    except ValueError:
        return str(p)


def plan(layout: Layout, with_sudoers: bool = False) -> list[Action]:
    h = helpers_dir()
    state, engines = layout.state_dir, layout.engines_dir

    def rel(p: Path) -> str:
        return _rel(layout, p)

    cfg = (
        f'# SPDX-License-Identifier: MIT\n# written by ailx system install\n[paths]\nstate_dir = "{rel(state)}"\n'
        f'engines_dir = "{rel(engines)}"\nhelper = "{HELPER}"\n'
    ).encode()
    acts = [
        Action("group", GROUP, note="operators allowed to use the privileged helper and RAPL collector"),
        Action("dir", "/etc/ailx", 0o755),
        Action("dir", "/etc/ailx/profiles", 0o755, note="local profiles override built-ins"),
        Action("dir", rel(state), 0o2770, GROUP),
        Action("dir", rel(state / "transactions"), 0o2770, GROUP),
        Action("dir", rel(engines.parent), 0o755) if rel(engines.parent) != "/" else None,
        Action("dir", rel(engines), 0o2775, GROUP),
        Action("dir", "/usr/local/libexec", 0o755),
        Action("file", HELPER, 0o755, content=_with_isolated_shebang(h / "apply-helper/ailx_apply_helper.py")),
        Action("file", RAPL, 0o755, content=_with_isolated_shebang(h / "rapl-collector/ailx_rapl_collector.py")),
        Action(
            "file",
            RAPL_UNIT,
            0o644,
            content=(h / "rapl-collector/ailx-rapl-collector.service").read_bytes(),
            note="installed, not enabled",
        ),
        Action("file", "/etc/ailx/ailx.toml", 0o644, content=cfg),
    ]
    if with_sudoers:
        acts.append(
            Action(
                "file",
                SUDOERS,
                0o440,
                content=f"# written by ailx system install\n%{GROUP} ALL=(root) NOPASSWD: {HELPER}\n".encode(),
                note="members of group ailx may run exactly the helper without a password",
            )
        )
    if sys.prefix != sys.base_prefix:
        for name in ("ailx", "ailx-bench"):
            exe = Path(sys.prefix) / "bin" / name
            if exe.exists():
                acts.append(Action("symlink", f"/usr/local/bin/{name}", target=str(exe)))
    acts.append(Action("exec", "systemctl daemon-reload", note="live root only"))
    return [a for a in acts if a is not None]


def _manifest_path(layout: Layout) -> Path:
    return layout.state_dir / "install-manifest.json"


def _gid(group: str) -> int | None:
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError:
        return None


def install(layout: Layout, with_sudoers: bool = False, force: bool = False, dry_run: bool = False, log=print) -> Doc:  # type: ignore[no-untyped-def]
    live = str(layout.root) == "/"
    if live and os.geteuid() != 0 and not dry_run:
        raise InstallError("system install needs root (sudo ailx system install)")
    acts = plan(layout, with_sudoers)
    if dry_run:
        for a in acts:
            log("would " + a.describe())
        return {"dry_run": True, "actions": [a.describe() for a in acts]}
    old = json.loads(_manifest_path(layout).read_text()) if _manifest_path(layout).is_file() else {"items": []}
    owned = {i["path"] for i in old["items"]}
    items: list[Doc] = []
    for a in acts:
        host = layout.host(a.path) if a.kind != "exec" else None
        rec: Doc = {"kind": a.kind, "path": a.path, "preexisting": False}
        if a.kind == "group":
            if not live:
                rec["skipped"] = "test root"
            elif _gid(GROUP) is None:
                subprocess.run(["groupadd", "--system", GROUP], check=True)
                rec["created"] = True
            else:
                rec["preexisting"] = a.path not in owned
        elif a.kind == "dir":
            assert host is not None
            rec["preexisting"] = host.exists() and a.path not in owned
            host.mkdir(parents=True, exist_ok=True)
            if not rec["preexisting"]:
                os.chmod(host, a.mode or 0o755)
                gid = _gid(a.group) if (a.group and live) else None
                if gid is not None:
                    os.chown(host, 0, gid)
        elif a.kind == "file":
            assert host is not None and a.content is not None
            digest = sha256_hex(a.content)
            rec["sha256"] = digest
            if host.exists() and a.path not in owned and sha256_hex(host.read_bytes()) != digest:
                if not force:
                    raise InstallError(f"{a.path} exists with different content (use --force to back it up)")
                shutil.copy2(host, str(host) + ".ailx-bak")
                rec["backup"] = a.path + ".ailx-bak"
            if a.path == SUDOERS and shutil.which("visudo"):
                tmp = host.with_name(".ailx.sudoers.check")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(a.content)
                r = subprocess.run(["visudo", "-cf", str(tmp)], capture_output=True, text=True)
                tmp.unlink()
                if r.returncode != 0:
                    raise InstallError(f"sudoers validation failed: {r.stdout}{r.stderr}")
            host.parent.mkdir(parents=True, exist_ok=True)
            tmp = host.with_name(f".{host.name}.ailx-tmp")
            tmp.write_bytes(a.content)
            os.chmod(tmp, a.mode or 0o644)
            os.replace(tmp, host)
        elif a.kind == "symlink":
            assert host is not None and a.target
            if host.exists() and not host.is_symlink() and not force:
                raise InstallError(f"{a.path} exists and is not a symlink")
            host.parent.mkdir(parents=True, exist_ok=True)
            if host.is_symlink() or host.exists():
                host.unlink()
            host.symlink_to(a.target)
            rec["target"] = a.target
        elif a.kind == "exec":
            if live and shutil.which("systemctl"):
                subprocess.run(["systemctl", "daemon-reload"], check=False)
            else:
                rec["skipped"] = "test root or no systemd"
        log("done  " + a.describe())
        items.append(rec)
    manifest = {
        "schema": "ailx.install-manifest/v0",
        "installed_at": utcnow(),
        "root": str(layout.root),
        "with_sudoers": with_sudoers,
        "items": items,
    }
    _manifest_path(layout).parent.mkdir(parents=True, exist_ok=True)
    _manifest_path(layout).write_text(json.dumps(manifest, indent=2))
    return manifest


def uninstall(layout: Layout, purge: bool = False, force: bool = False, dry_run: bool = False, log=print) -> Doc:  # type: ignore[no-untyped-def]
    from ailx.system.transactions import Manager

    live = str(layout.root) == "/"
    mpath = _manifest_path(layout)
    if not mpath.is_file():
        raise InstallError(f"no install manifest at {mpath}; nothing recorded to remove")
    if live and os.geteuid() != 0 and not dry_run:
        raise InstallError("system uninstall needs root")
    st = Manager(layout).state()
    if st.get("active_txn") and not force:
        raise InstallError(f"profile {st['active_profile']} is active; run 'ailx rollback' first (or --force)")
    manifest = json.loads(mpath.read_text())
    data_dirs = {_rel(layout, layout.state_dir), _rel(layout, layout.engines_dir)}
    kept: list[str] = []
    removed: list[str] = []
    for it in reversed(manifest["items"]):
        if it.get("preexisting") or it.get("skipped"):
            continue
        host = layout.host(it["path"]) if it["kind"] not in ("exec", "group") else None
        if it["kind"] == "file":
            assert host is not None
            if not host.exists():
                continue
            if sha256_hex(host.read_bytes()) != it["sha256"] and not force:
                kept.append(f"{it['path']} (modified since install)")
                continue
            if not dry_run:
                host.unlink()
                if it.get("backup") and layout.host(it["backup"]).exists():
                    os.replace(layout.host(it["backup"]), host)
            removed.append(it["path"])
        elif it["kind"] == "symlink":
            assert host is not None
            if host.is_symlink() and os.readlink(host) == it.get("target"):
                if not dry_run:
                    host.unlink()
                removed.append(it["path"])
        elif it["kind"] == "dir":
            assert host is not None
            if purge and it["path"] in data_dirs:
                if not dry_run:
                    shutil.rmtree(host, ignore_errors=True)
                removed.append(it["path"] + " (purged)")
                continue
            if host.is_dir():
                leftovers = [p for p in host.iterdir() if p != mpath]
                if leftovers:
                    kept.append(f"{it['path']} (not empty)")
                    continue
                if not dry_run and host != mpath.parent:
                    host.rmdir()
                    removed.append(it["path"])
        elif it["kind"] == "group" and purge and live and it.get("created") and not dry_run:
            subprocess.run(["groupdel", GROUP], check=False)
            removed.append("group ailx")
    if not dry_run and mpath.exists():
        mpath.unlink()
        if purge and mpath.parent.is_dir() and not any(mpath.parent.iterdir()):
            mpath.parent.rmdir()
    for k in kept:
        log("kept   " + k)
    for r in removed:
        log(("would remove " if dry_run else "removed ") + r)
    return {"removed": removed, "kept": kept, "dry_run": dry_run}


def describe(actions: list[Action]) -> list[Doc]:
    return [asdict(a) | {"content": None} for a in actions]
