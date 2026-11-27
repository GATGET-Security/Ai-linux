# SPDX-License-Identifier: MIT
"""``ailx`` — the AI Linux system-layer CLI (separate from the measurement CLI ``ailx-bench``)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from ailx import PACKAGE_VERSION, __version__
from ailx.probe import collect_hardware_profile
from ailx.system import doctor, knobs, profiles, resources
from ailx.system.engines import EngineError, EngineManager, catalog
from ailx.system.image import ImageError, ImageManager
from ailx.system.install import GROUP, InstallError, packaged, sudoers_disable, sudoers_enable
from ailx.system.install import install as sys_install
from ailx.system.install import plan as install_plan
from ailx.system.install import uninstall as sys_uninstall
from ailx.system.integration import platform_info
from ailx.system.paths import Layout, load_layout, system_layout
from ailx.system.profiles import ProfileError
from ailx.system.transactions import Manager, TransactionError

GiB = 1024**3


def _out(a: argparse.Namespace, obj: Any, human: str) -> None:
    print(json.dumps(obj, indent=2, default=str) if a.json else human)


def _gib(x: int | None) -> str:
    return "n/a" if x is None else f"{x / GiB:.1f} GiB"


def cmd_hardware(a: argparse.Namespace, lay: Layout) -> int:
    h = collect_hardware_profile(lay.root, use_nvml=not a.no_nvml)
    cpu = h["cpu"]
    lines = [
        f"Hardware profile {h['id'][:16]}",
        f"CPU     {cpu['model']} — {cpu['logical_cpus']} logical CPUs, SMT {cpu['smt_active']}",
    ]
    for d in cpu["llc_domains"]:
        lines.append(
            f"  LLC {d['id']}: {(d['size_bytes'] or 0) // 2**20} MiB"
            f" shared by CPUs {d['cpus'][0]}–{d['cpus'][-1]} ({len(d['cpus'])})"
        )
    lines.append(f"Memory  {_gib(h['memory']['total_bytes'])}, NUMA nodes {h['memory']['numa_nodes']}")
    for acc in h["accelerators"]:
        mem = ", ".join(f"{m['kind']} {_gib(m['size_bytes'])}" for m in acc["memory_domains"])
        link = acc["pcie"] or {}
        lines.append(
            f"GPU     {acc['vendor']} {acc['model'] or acc['device_id']} at {acc['pci_address']} driver "
            f"{acc['driver']} — {mem}; PCIe {link.get('current_speed')} x{link.get('current_width')}"
        )
    if not h["accelerators"]:
        lines.append("GPU     none detected")
    for s in h["storage"]:
        lines.append(f"Storage {s['name']} {s['model'] or ''} {s['transport'] or ''} {_gib(s['size_bytes'])}")
    if h["unavailable"]:
        lines.append("Unavailable: " + "; ".join(f"{k}: {v}" for k, v in h["unavailable"].items()))
    _out(a, h, "\n".join(lines))
    return 0


def cmd_resources(a: argparse.Namespace, lay: Layout) -> int:
    r = resources.snapshot(lay, use_nvml=not a.no_nvml)
    m, lim = r["memory"], r["limits"]
    lines = [
        f"CPU      {r['cpu']['logical_cpus']} logical, {r['cpu']['allowed_cpus']} allowed for this process,"
        f" load {r['cpu']['loadavg']}",
        f"Memory   {_gib(m['available_bytes'])} available of {_gib(m['total_bytes'])};"
        f" swap {_gib(m['swap_free_bytes'])} free of {_gib(m['swap_total_bytes'])}",
        f"cgroup   {lim.get('cgroup')} memory.max={_gib(lim.get('memory_max_bytes'))}"
        f" cpu quota={lim.get('cpu_quota_cpus')}",
    ]
    for g in r["gpu_live"]:
        lines.append(
            f"GPU      [{g['source']}] {g.get('name') or g.get('card')}: VRAM {_gib(g['vram_free_bytes'])} free"
            f" of {_gib(g['vram_total_bytes'])}"
        )
    if not r["gpu_live"]:
        lines.append(
            "GPU      no live GPU data"
            + (f" ({r['unavailable'].get('gpu_live')})" if "gpu_live" in r["unavailable"] else "")
        )
    _out(a, r, "\n".join(lines))
    return 0


def cmd_doctor(a: argparse.Namespace, lay: Layout) -> int:
    checks = doctor.run(lay, use_nvml=not a.no_nvml)
    sym = {"ok": "✔", "info": "ℹ", "warn": "!", "fail": "✘", "skip": "–"}
    lines = [
        f"{sym[c.status]} {c.status:<4} {c.id:<28} {c.summary}" + (f"\n{'':36}→ {c.remedy}" if c.remedy else "")
        for c in checks
    ]
    _out(a, doctor.as_dicts(checks), "\n".join(lines))
    return 1 if any(c.status == "fail" for c in checks) else 0


def cmd_status(a: argparse.Namespace, lay: Layout) -> int:
    mgr = Manager(lay)
    st = mgr.state()
    info = platform_info(lay)
    drift = mgr.drift() if st.get("active_txn") else []
    h = collect_hardware_profile(lay.root, use_nvml=not a.no_nvml)
    obj: dict[str, Any] = {
        "version": __version__,
        "mode": lay.mode,
        "root": str(lay.root),
        "state_dir": str(lay.state_dir),
        "engines_dir": str(lay.engines_dir),
        "models_dir": str(lay.models_dir),
        "platform": info,
        "drift": drift,
        "interrupted": [t["id"] for t in mgr.interrupted()],
        "hardware": {"id": h["id"], "cpu": h["cpu"]["model"], "gpus": [x["vendor"] for x in h["accelerators"]]},
    }
    lines = [
        f"ailx {__version__} ({lay.mode} mode)  state {lay.state_dir}",
        f"hardware {h['cpu']['model']}; GPUs: {', '.join(obj['hardware']['gpus']) or 'none'}",
        f"profile  {st.get('active_profile') or 'none (stock)'}"
        + (
            f"  [{info['txn_status']}, {len(info['applied'])} value(s) applied, txn {st['active_txn']}]"
            if st.get("active_txn")
            else ""
        ),
    ]
    for d in drift:
        lines.append(f"  DRIFT {d['knob']} {d['target']}: expected {d['expected']!r}, now {d['current']!r}")
    for t in obj["interrupted"]:
        lines.append(f"  INTERRUPTED transaction {t} → ailx rollback --recover")
    for e in info["engines"]:
        lines.append(
            f"engine   {e['engine']}/{e['install_id']}{' (current)' if e['current'] else ''}"
            f" manifest {e['manifest_sha256'][:12]}"
        )
    if not info["engines"]:
        lines.append("engine   none installed")
    _out(a, obj, "\n".join(lines))
    return 1 if obj["interrupted"] else 0


def _plan(a: argparse.Namespace, lay: Layout) -> profiles.Plan:
    prof = profiles.load(lay, a.name)
    return profiles.plan(lay, prof, include_experimental=a.include_experimental, allow_opt_in=a.allow_opt_in)


def _plan_text(p: profiles.Plan) -> str:
    lines = [f"Plan for profile '{p.profile}' (experimental {'included' if p.include_experimental else 'excluded'}):"]
    for s in p.steps:
        tgt = s.target or "-"
        if s.action == "change":
            lines.append(
                f"  CHANGE  {s.knob:<34} {tgt}: {s.current!r} -> {s.desired!r}"
                + ("  [EXPERIMENTAL]" if s.experimental else "")
            )
        elif s.action == "no-change":
            lines.append(f"  ok      {s.knob:<34} {tgt}: already {s.current!r}")
        else:
            lines.append(f"  skip    {s.knob:<34} {s.reason}")
    if not p.steps:
        lines.append("  (no settings — stock configuration)")
    lines += [f"WARNING: {w}" for w in p.warnings]
    lines.append(f"{len(p.changes)} change(s).")
    return "\n".join(lines)


def cmd_profile(a: argparse.Namespace, lay: Layout) -> int:
    if a.sub == "list":
        ps = profiles.load_all(lay)
        rows = [
            {
                "name": n,
                "description": p["description"],
                "settings": len(p["settings"]),
                "experimental": sum(1 for s in p["settings"] if s["experimental"]),
            }
            for n, p in sorted(ps.items())
        ]
        _out(
            a,
            rows,
            "\n".join(
                f"{r['name']:<18} {r['settings']} setting(s), {r['experimental']} experimental — {r['description']}"
                for r in rows
            ),
        )
        return 0
    if a.sub == "show":
        doc = profiles.load(lay, a.name)
        _out(a, doc, json.dumps(doc, indent=2))
        return 0
    p = _plan(a, lay)
    if a.sub == "plan" or a.dry_run:
        _out(a, p.as_dict(), _plan_text(p) + ("\n(dry run: nothing changed)" if a.sub == "apply" else ""))
        return 0
    if not a.yes:
        print(_plan_text(p) + "\nRe-run with --yes to apply (or --dry-run to only show the plan).")
        return 2
    txn = Manager(lay).apply(p, confirm_timeout=a.confirm_within, replace=a.replace)
    msg = f"{txn['id']}: {txn['status']}, {len(txn['changes'])} change(s) applied and verified"
    if txn["status"] == "pending-confirmation":
        msg += (
            f"\nConfirm within {a.confirm_within:.0f} s with 'ailx confirm', otherwise it is rolled back automatically."
        )
    _out(a, txn, _plan_text(p) + "\n" + msg)
    return 0


def cmd_confirm(a: argparse.Namespace, lay: Layout) -> int:
    t = Manager(lay).confirm(a.txn)
    _out(a, t, f"{t['id']} committed")
    return 0


def cmd_rollback(a: argparse.Namespace, lay: Layout) -> int:
    mgr = Manager(lay)
    if a.recover:
        done = mgr.recover()
        _out(a, done, "\n".join(f"{t['id']}: {t['status']}" for t in done) or "no interrupted transactions")
        return 0
    t = mgr.rollback(a.txn, force=a.force)
    skipped = [e["msg"] for e in t.get("events", []) if e["msg"].startswith("drift")]
    _out(a, t, f"{t['id']}: {t['status']}" + "".join(f"\n  {s}" for s in skipped))
    return 0 if t["status"] == "rolled-back" else 1


def cmd_history(a: argparse.Namespace, lay: Layout) -> int:
    mgr = Manager(lay)
    if a.txn:
        t = mgr.load(a.txn)
        _out(a, t, json.dumps(t, indent=2))
        return 0
    ts = mgr.all()
    _out(
        a,
        ts,
        "\n".join(f"{t['id']}  {t['status']:<22} {t['profile']:<18} {len(t['changes'])} change(s)" for t in ts)
        or "no transactions",
    )
    return 0


def cmd_knobs(a: argparse.Namespace, lay: Layout) -> int:
    rows = []
    for k in knobs.registry().values():
        tg = knobs.targets(lay, k)
        cur = sorted({str(knobs.read(lay, k, t.target)) for t in tg})
        rows.append(
            {
                "id": k["id"],
                "tier": k["tier"],
                "security": k["security_class"],
                "validation": k["validation_status"],
                "available": bool(tg),
                "current": cur,
            }
        )
    _out(
        a,
        rows,
        "\n".join(
            f"{r['id']:<34} {r['tier']} {r['security']:<15} {r['validation']:<12} "
            f"{'available' if r['available'] else 'n/a':<9} {','.join(r['current'])}"
            for r in rows
        ),
    )
    return 0


def cmd_engine(a: argparse.Namespace, lay: Layout) -> int:
    em = EngineManager(lay)
    if a.sub == "list":
        inst = em.installs()
        cat = catalog()
        lines = [
            "catalog: "
            + ", ".join(
                f"{n} {str(s.get('ref') or s.get('version'))[:12]} [{', '.join(s['variants'])}]" for n, s in cat.items()
            )
        ]
        for m in inst:
            cur = em.current(m["engine"])
            flag = " (current)" if cur and cur["install_id"] == m.get("install_id") else ""
            lines.append(f"{m['engine']}/{m['install_id']}{flag}" + (" BROKEN" if m.get("broken") else ""))
        _out(a, {"catalog": cat, "installs": inst}, "\n".join(lines))
        return 0
    if a.sub == "install":
        m = em.install(
            a.engine,
            a.variant,
            source=a.source,
            jobs=a.jobs,
            targets=a.targets.split(",") if a.targets else None,
            log=(lambda s: None) if a.json else print,
        )
        _out(a, m, f"installed {m['engine']}/{m['install_id']} ({len(m['artifacts'])} artifact(s))")
        return 0
    if a.sub == "verify":
        v = em.verify(a.engine, a.install_id, full=a.full)
        _out(
            a,
            v,
            f"{v['engine']}/{v['install_id']}: {'OK' if v['ok'] else 'FAILED'}"
            + "".join(f"\n  {p}" for p in v["problems"][:20]),
        )
        return 0 if v["ok"] else 1
    if a.sub == "remove":
        em.remove(a.engine, a.install_id)
        _out(a, {"removed": f"{a.engine}/{a.install_id}"}, f"removed {a.engine}/{a.install_id}")
        return 0
    if a.sub == "use":
        em.use(a.engine, a.install_id)
        _out(a, {"current": f"{a.engine}/{a.install_id}"}, f"{a.engine} -> {a.install_id}")
        return 0
    env = em.env()
    _out(a, env, "\n".join(f"export {k}={v}" for k, v in env.items()) or "# no engines installed")
    return 0


def cmd_system(a: argparse.Namespace, lay: Layout) -> int:
    slay = system_layout()
    log = (lambda s: None) if a.json else print
    if a.sub == "sudoers":
        if a.action == "enable":
            path = sudoers_enable(lay)
            _out(a, {"sudoers": str(path)}, f"wrote {path} (group {GROUP} may run {lay.helper} without password)")
        else:
            removed = sudoers_disable(lay)
            _out(a, {"removed": removed}, "removed" if removed else "no ailx sudoers rule present")
        return 0
    if a.sub in ("install", "uninstall") and packaged():
        raise InstallError("this system layer is installed from the ailx .deb package; use apt/dpkg instead")
    if a.sub == "plan":
        acts = install_plan(slay, with_sudoers=a.with_sudoers)
        _out(a, [x.describe() for x in acts], "\n".join(x.describe() for x in acts))
        return 0
    if a.sub == "install":
        res = sys_install(slay, with_sudoers=a.with_sudoers, force=a.force, dry_run=a.dry_run, log=log)
    else:
        res = sys_uninstall(slay, purge=a.purge, force=a.force, dry_run=a.dry_run, log=log)
    if a.json:
        print(json.dumps(res, indent=2))
    return 0


def _health_checks(lay: Layout) -> list[tuple[str, bool]]:
    import subprocess

    checks = doctor.run(lay, use_nvml=False)
    # this check runs as part of boot, so "is-system-running" would still say "starting": use failed units
    failed = subprocess.run(["systemctl", "--failed", "--no-legend", "--plain"], capture_output=True, text=True)
    bad = [ln.split()[0] for ln in failed.stdout.splitlines() if ln.strip() and "ailx-boot-health" not in ln]
    data = lay.host("/var/lib/ailx")
    return [
        ("doctor-no-fail", not any(c.status == "fail" for c in checks)),
        ("no-failed-units" + (f":{','.join(bad)}" if bad else ""), failed.returncode == 0 and not bad),
        ("data-partition", data.is_dir() and os.access(data, os.W_OK)),
    ]


def cmd_image(a: argparse.Namespace, lay: Layout) -> int:
    im = ImageManager(lay)
    if a.sub == "status":
        st = im.status()
        if not st["image"]:
            _out(a, st, st["reason"])
            return 0
        rel = st["release"]
        trial = " TRIAL BOOT" if st["trial_boot"] else ""
        lines = [
            f"slot     {st['current_slot']} (default {st.get('default_slot', '?')}){trial}",
            f"version  {rel.get('VERSION', '?')} build {rel.get('BUILD_ID', '?')}",
        ]
        if "note" in st:
            lines.append(f"note     {st['note']}")
            _out(a, st, "\n".join(lines))
            return 0
        oth = st["other_slot"]
        lines.append(f"other    slot {oth['slot']}: {oth['release'].get('VERSION', 'unknown')}")
        if st["next_slot"]:
            lines.append(f"armed    next boot tries slot {st['next_slot']} once")
        if st["last_trial"]:
            lines.append(f"last trial: {st['last_trial']}")
        _out(a, st, "\n".join(lines))
        return 0
    if a.sub == "stage":
        _out(a, im.stage(a.source, a.sha256, log=(lambda s: None) if a.json else print), "")
        return 0
    if a.sub == "confirm":
        _out(a, im.confirm(), "confirmed: current slot is now the default")
        return 0
    if a.sub == "rollback":
        r = im.rollback()
        _out(a, r, f"default slot is now {r['default_slot']}; reboot to switch")
        return 0
    r = im.health(_health_checks(lay))
    _out(a, r, json.dumps(r))
    return 0 if (not r["trial"] or r["confirmed"]) else 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ailx", description="AI Linux system layer")
    p.add_argument(
        "--version",
        action="version",
        version=__version__ + (f" (package {PACKAGE_VERSION})" if PACKAGE_VERSION else ""),
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-nvml", action="store_true", help="do not use NVML")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="system-layer state, drift, engines").set_defaults(fn=cmd_status)
    sub.add_parser("hardware", help="hardware inventory").set_defaults(fn=cmd_hardware)
    sub.add_parser("resources", help="available CPU/RAM/GPU/VRAM now").set_defaults(fn=cmd_resources)
    sub.add_parser("doctor", help="diagnose host and system layer").set_defaults(fn=cmd_doctor)
    sub.add_parser("knobs", help="knob registry with availability and current values").set_defaults(fn=cmd_knobs)

    pr = sub.add_parser("profile", help="declarative system profiles").add_subparsers(dest="sub", required=True)
    pr.add_parser("list").set_defaults(fn=cmd_profile)
    x = pr.add_parser("show")
    x.add_argument("name")
    x.set_defaults(fn=cmd_profile)
    for name in ("plan", "apply"):
        x = pr.add_parser(name)
        x.add_argument("name")
        x.add_argument(
            "--include-experimental", action="store_true", help="also apply settings that are not hardware-validated"
        )
        x.add_argument("--allow-opt-in", action="store_true", help="allow knobs classified explicit-opt-in")
        if name == "apply":
            x.add_argument("--dry-run", action="store_true")
            x.add_argument("--yes", action="store_true", help="really apply")
            x.add_argument("--replace", action="store_true", help="roll back the active profile first")
            x.add_argument(
                "--confirm-within",
                type=float,
                metavar="SECONDS",
                help="dead-man timer: roll back automatically unless 'ailx confirm' runs in time",
            )
        x.set_defaults(fn=cmd_profile, dry_run=False)

    x = sub.add_parser("confirm", help="confirm a pending transaction")
    x.add_argument("txn", nargs="?")
    x.set_defaults(fn=cmd_confirm)
    x = sub.add_parser("rollback", help="restore values from before the active transaction")
    x.add_argument("txn", nargs="?")
    x.add_argument("--force", action="store_true", help="also overwrite values changed by others since apply")
    x.add_argument("--recover", action="store_true", help="recover transactions interrupted mid-apply")
    x.set_defaults(fn=cmd_rollback)
    x = sub.add_parser("history", help="transaction journal")
    x.add_argument("txn", nargs="?")
    x.set_defaults(fn=cmd_history)

    en = sub.add_parser("engine", help="inference engines").add_subparsers(dest="sub", required=True)
    en.add_parser("list").set_defaults(fn=cmd_engine)
    en.add_parser("env", help="shell exports for ailx-bench workloads").set_defaults(fn=cmd_engine)
    x = en.add_parser("install")
    x.add_argument("engine")
    x.add_argument("--variant", default="cpu")
    x.add_argument("--source", help="alternative git source (mirror/local clone) — recorded in the manifest")
    x.add_argument("--jobs", type=int)
    x.add_argument("--targets", help="comma-separated subset of build targets")
    x.set_defaults(fn=cmd_engine)
    x = en.add_parser("verify")
    x.add_argument("engine")
    x.add_argument("install_id", nargs="?")
    x.add_argument("--full", action="store_true", help="pip engines: re-hash every installed file")
    x.set_defaults(fn=cmd_engine)
    for name in ("remove", "use"):
        x = en.add_parser(name)
        x.add_argument("engine")
        x.add_argument("install_id")
        x.set_defaults(fn=cmd_engine)

    im = sub.add_parser("image", help="A/B system versions (reference image only)").add_subparsers(
        dest="sub", required=True
    )
    im.add_parser("status").set_defaults(fn=cmd_image)
    x = im.add_parser("stage", help="write a root filesystem image to the inactive slot and arm one trial boot")
    x.add_argument("source", help="root filesystem image file or block device")
    x.add_argument("--sha256", help="expected sha256 of the image (verified before arming)")
    x.set_defaults(fn=cmd_image)
    im.add_parser("confirm").set_defaults(fn=cmd_image)
    im.add_parser("rollback").set_defaults(fn=cmd_image)
    im.add_parser("health", help="boot-time health check (confirms a trial boot)").set_defaults(fn=cmd_image)
    from ailx.compute.cli import register as register_compute

    register_compute(sub)
    sy = sub.add_parser("system", help="install/uninstall the system layer").add_subparsers(dest="sub", required=True)
    x = sy.add_parser("plan")
    x.add_argument("--with-sudoers", action="store_true")
    x.set_defaults(fn=cmd_system)
    x = sy.add_parser("install")
    x.add_argument("--with-sudoers", action="store_true", help="let group ailx run the helper without password")
    x.add_argument("--force", action="store_true")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_system)
    x = sy.add_parser("sudoers", help="enable/disable passwordless helper use for group ailx")
    x.add_argument("action", choices=["enable", "disable"])
    x.set_defaults(fn=cmd_system)
    x = sy.add_parser("uninstall")
    x.add_argument("--purge", action="store_true", help="also remove state, engines and the ailx group")
    x.add_argument("--force", action="store_true")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_system)
    return p


def main(argv: list[str] | None = None) -> int:
    from ailx.compute.cli import ERRORS as COMPUTE_ERRORS

    a = build_parser().parse_args(argv)
    try:
        rc: int = a.fn(a, load_layout())
        return rc
    except (ProfileError, TransactionError, EngineError, InstallError, ImageError, KeyError, *COMPUTE_ERRORS) as exc:
        if a.json:
            print(json.dumps({"error": str(exc), "kind": type(exc).__name__}))
        else:
            print(f"ailx: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
