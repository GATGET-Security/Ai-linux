# SPDX-License-Identifier: MIT
"""``ailx doctor``: diagnosis of the host, the system layer and measurement readiness.

Every check returns ok / info / warn / fail / skip with a remedy. ``fail`` means the system layer
cannot work correctly (e.g. an interrupted transaction); missing GPUs are ``skip``, not failures.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

from ailx.core import Doc
from ailx.probe import collect_fingerprint, collect_hardware_profile
from ailx.probe.fingerprint import HashCache
from ailx.system import knobs
from ailx.system.engines import EngineError, EngineManager
from ailx.system.paths import Layout
from ailx.system.transactions import Manager


@dataclass
class Check:
    id: str
    status: str  # ok | info | warn | fail | skip
    summary: str
    remedy: str = ""


def run(layout: Layout, use_nvml: bool = True) -> list[Check]:
    c: list[Check] = []
    add = c.append
    add(
        Check(
            "python",
            "ok" if sys.version_info >= (3, 12) else "fail",
            f"Python {sys.version.split()[0]}",
            "" if sys.version_info >= (3, 12) else "Python >= 3.12 required",
        )
    )

    hwp = collect_hardware_profile(layout.root, use_nvml=use_nvml)
    fp = collect_fingerprint(
        hwp["id"], layout.root, use_nvml=use_nvml, hash_cache=HashCache(layout.state_dir / "hash-cache.json")
    )
    osr = fp["os"]
    is_ref = osr.get("id") == "ubuntu" and osr.get("version_id") == "26.04"
    add(
        Check(
            "host-os",
            "ok" if is_ref else "info",
            f"{osr.get('pretty_name') or osr.get('id')} / kernel {fp['kernel']['release']}",
            "" if is_ref else "reference host is Ubuntu 26.04 LTS (ADR-0006)",
        )
    )

    sched = fp["sched"]
    add(
        Check(
            "sched-ext",
            "ok" if sched.get("sched_ext_available") else "info",
            "sched_ext available" if sched.get("sched_ext_available") else "sched_ext not available in this kernel",
            "" if sched.get("sched_ext_available") else "needed only for experimental scheduler profiles (Phase 2)",
        )
    )
    add(
        Check(
            "sched-cache",
            "info",
            f"SCHED_CACHE config: {sched.get('sched_cache_config') or 'absent/unknown'}; toggle "
            f"{'readable' if sched.get('sched_cache_enabled') is not None else 'not readable (debugfs)'}",
            "kernels >= 7.2 pack few-thread processes into one LLC (H-K-01); measure with E3",
        )
    )

    sec = fp["security"]
    vulnerable = [k for k, v in sec["mitigations"].items() if v.startswith("Vulnerable")]
    add(
        Check(
            "cpu-mitigations",
            "warn" if vulnerable else "ok",
            f"vulnerable: {', '.join(vulnerable)}" if vulnerable else "no 'Vulnerable' entries",
            "AI Linux never disables mitigations (ADR-0004); check BIOS/microcode/kernel" if vulnerable else "",
        )
    )
    add(
        Check(
            "secure-boot",
            "info",
            f"secure_boot={sec['secure_boot']} lockdown={sec['lockdown']}",
            "with lockdown=integrity, debugfs-only knobs (e.g. SCHED_CACHE) cannot be changed — by design",
        )
    )

    nv = shutil.which("nvidia-smi")
    gpus = [a for a in hwp["accelerators"] if a["vendor"] == "nvidia"]
    if gpus and not nv:
        add(
            Check(
                "nvidia-driver",
                "warn",
                f"{len(gpus)} NVIDIA GPU(s) on PCI but nvidia-smi missing",
                "sudo ubuntu-drivers install",
            )
        )
    elif gpus:
        drv = next((s.get("driver_version") for s in fp["accelerator_state"]), None)
        add(
            Check(
                "nvidia-driver",
                "ok" if drv else "warn",
                f"{len(gpus)} NVIDIA GPU(s), driver {drv or 'unknown'}",
                "" if drv else "install the 'nvidia' extra (nvidia-ml-py) for NVML telemetry",
            )
        )
    else:
        add(Check("nvidia-driver", "skip", "no NVIDIA GPU detected", "GPU-dependent validation stays hw-queued"))

    conflicts = {s for k in knobs.registry().values() for s in k.get("conflicts_with_services", [])}
    active = {n: s for n, s in fp["services"]["tuning_daemons"].items() if s == "active"}
    clash = sorted(set(active) & conflicts)
    add(
        Check(
            "tuning-daemons",
            "warn" if clash else "ok",
            f"active: {', '.join(sorted(active)) or 'none'}",
            f"{', '.join(clash)} manage(s) knobs that profiles skip; AI Linux does not fight other tuners"
            if clash
            else "",
        )
    )
    if "unattended-upgrades" in active:
        add(
            Check(
                "measurement-hygiene",
                "info",
                "unattended-upgrades is active",
                "stop it during measurement campaigns (runbook §3)",
            )
        )

    mgr = Manager(layout)
    stuck = mgr.interrupted()
    add(
        Check(
            "transactions",
            "fail" if stuck else "ok",
            f"{len(stuck)} interrupted transaction(s)" if stuck else f"{len(mgr.all())} transaction(s) in journal",
            "run 'ailx rollback --recover'" if stuck else "",
        )
    )
    st = mgr.state()
    if st.get("active_txn"):
        txn = mgr.load(st["active_txn"])
        drift = mgr.drift(txn)
        add(
            Check(
                "active-profile",
                "warn" if drift else "ok",
                f"profile {st['active_profile']} ({txn['status']}), drift on {len(drift)} target(s)",
                "another tool changed applied values; 'ailx status' shows details" if drift else "",
            )
        )
        if txn["status"] == "pending-confirmation" and time.time() > float(txn["confirm_deadline"] or 0) + 30:
            add(
                Check(
                    "watchdog",
                    "fail",
                    f"{txn['id']} passed its confirmation deadline but was not rolled back",
                    "run 'ailx rollback'",
                )
            )
    else:
        add(Check("active-profile", "ok", "no profile applied (stock configuration)"))

    em = EngineManager(layout)
    installs = em.installs()
    if not installs:
        add(Check("engines", "info", "no engines installed", "ailx engine install llama.cpp --variant cuda"))
    for inst in installs:
        if inst.get("broken"):
            add(
                Check(
                    f"engine:{inst['engine']}/{inst['install_id']}",
                    "fail",
                    "incomplete installation",
                    f"ailx engine remove {inst['engine']} {inst['install_id']}",
                )
            )
            continue
        try:
            v = em.verify(inst["engine"], inst["install_id"])
            add(
                Check(
                    f"engine:{inst['engine']}/{inst['install_id']}",
                    "ok" if v["ok"] else "fail",
                    "integrity ok" if v["ok"] else "; ".join(v["problems"][:3]),
                    "" if v["ok"] else "reinstall: ailx engine remove … && ailx engine install …",
                )
            )
        except EngineError as exc:
            add(Check(f"engine:{inst['engine']}", "fail", str(exc)))

    helper = layout.host(layout.helper)
    if str(layout.root) == "/" and os.geteuid() != 0:
        ok = helper.is_file() and subprocess.run(["sudo", "-n", "-l", str(helper)], capture_output=True).returncode == 0
        add(
            Check(
                "privileged-helper",
                "ok" if ok else "info",
                "helper usable via sudo -n" if ok else f"{helper} not usable without a password",
                "" if ok else "ailx system install --with-sudoers (or run profile commands with sudo)",
            )
        )
    else:
        add(Check("privileged-helper", "info", "running as root or under a test root: writes are direct"))
    rapl = os.environ.get("AILX_RAPL_SOCKET", "/run/ailx-rapl/rapl.sock")
    add(
        Check(
            "rapl-collector",
            "ok" if layout.host(rapl).exists() else "info",
            "RAPL collector socket present" if layout.host(rapl).exists() else "RAPL collector not running",
            "" if layout.host(rapl).exists() else "optional: CPU energy telemetry (helpers/rapl-collector)",
        )
    )
    return c


def as_dicts(checks: list[Check]) -> list[Doc]:
    return [asdict(x) for x in checks]
