# SPDX-License-Identifier: MIT
"""Declarative system profiles and change plans.

Activation rule (ADR-0008, ADR-0010): a setting is *active by default* only if it is not marked
experimental **and** its knob is ``hw-validated``. Everything else is inactive unless the operator
passes ``include_experimental``. Forbidden knobs are rejected when a profile is loaded;
``explicit-opt-in`` knobs additionally require ``allow_opt_in``.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ailx.core import Doc, validate
from ailx.system import knobs
from ailx.system.paths import Layout


class ProfileError(ValueError):
    pass


@dataclass
class Step:
    knob: str
    target: str | None
    requested: str
    current: str | None
    desired: str | None
    action: str  # change | no-change | skip
    reason: str
    experimental: bool
    hypothesis_ids: list[str] = field(default_factory=list)


@dataclass
class Plan:
    profile: str
    include_experimental: bool
    steps: list[Step]
    warnings: list[str]

    @property
    def changes(self) -> list[Step]:
        return [s for s in self.steps if s.action == "change"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "include_experimental": self.include_experimental,
            "steps": [asdict(s) for s in self.steps],
            "warnings": self.warnings,
        }


def profile_dirs(layout: Layout) -> list[Path]:
    return [layout.host("/etc/ailx/profiles"), layout.state_dir / "profiles"]


def _check(doc: Doc, origin: str) -> Doc:
    validate(doc, "ailx.profile/v0")
    for s in doc["settings"]:
        k = knobs.get(s["knob"])
        if k["security_class"] == "forbidden":
            raise ProfileError(f"{origin}: knob {k['id']} is forbidden (ADR-0004) and cannot be part of a profile")
        if k["mechanism"]["type"] == "none":
            raise ProfileError(f"{origin}: knob {k['id']} cannot be applied by AI Linux")
    return doc


def load_all(layout: Layout) -> dict[str, Doc]:
    out: dict[str, Doc] = {}
    pkg = resources.files("ailx.system").joinpath("data/profiles")
    for entry in sorted(pkg.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            doc = tomllib.loads(entry.read_text())
            out[doc["name"]] = _check(doc, f"builtin:{entry.name}")
    for d in profile_dirs(layout):
        for p in sorted(d.glob("*.toml")) if d.is_dir() else []:
            doc = tomllib.loads(p.read_text())
            out[doc["name"]] = _check(doc, str(p))  # local profiles may override built-ins
    return out


def load(layout: Layout, name: str) -> Doc:
    profiles = load_all(layout)
    if name not in profiles:
        raise ProfileError(f"unknown profile {name!r}; available: {', '.join(sorted(profiles))}")
    return profiles[name]


def active_services(layout: Layout) -> set[str]:
    if str(layout.root) != "/" or not shutil.which("systemctl"):
        return set()
    names = {s for k in knobs.registry().values() for s in k.get("conflicts_with_services", [])}
    active = set()
    for n in sorted(names):
        try:
            r = subprocess.run(["systemctl", "is-active", n], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.stdout.strip() == "active":
            active.add(n)
    return active


def plan(
    layout: Layout,
    profile: Doc,
    include_experimental: bool = False,
    allow_opt_in: bool = False,
    services: set[str] | None = None,
) -> Plan:
    services = active_services(layout) if services is None else services
    steps: list[Step] = []
    warnings: list[str] = []
    for s in profile["settings"]:
        k = knobs.get(s["knob"])
        hyp = s.get("hypothesis_ids", [])
        validated = k["validation_status"] == "hw-validated"
        experimental = bool(s["experimental"]) or not validated

        def skip(
            reason: str,
            target: str | None = None,
            current: str | None = None,
            k: Doc = k,
            s: Doc = s,
            experimental: bool = experimental,
            hyp: list[str] = hyp,
        ) -> None:
            steps.append(Step(k["id"], target, s["value"], current, None, "skip", reason, experimental, hyp))

        if experimental and not include_experimental:
            skip("inactive: experimental / not hardware-validated (use --include-experimental to opt in)")
            continue
        if k["security_class"] == "explicit-opt-in" and not allow_opt_in:
            skip("requires explicit opt-in (security/stability trade-off)")
            continue
        conflict = sorted(set(k.get("conflicts_with_services", [])) & services)
        if conflict:
            skip(f"managed by active service(s) {', '.join(conflict)}; AI Linux does not fight other tuners")
            continue
        tgts = knobs.targets(layout, k)
        if not tgts:
            skip("not available on this system (interface missing)")
            continue
        for t in tgts:
            current = knobs.read(layout, k, t.target)
            try:
                desired = knobs.desired_value(k, s["value"], t.target)
            except ValueError as exc:
                skip(f"invalid: {exc}", t.target, current)
                continue
            if t.choices and desired not in t.choices:
                skip(f"kernel does not offer {desired!r} (offers {', '.join(t.choices)})", t.target, current)
                continue
            if current is None:
                skip("current value unreadable", t.target, None)
                continue
            action = "no-change" if current == desired else "change"
            steps.append(
                Step(
                    k["id"],
                    t.target,
                    s["value"],
                    current,
                    desired,
                    action,
                    "already set" if action == "no-change" else "will change",
                    experimental,
                    hyp,
                )
            )
    if include_experimental and any(st.experimental and st.action == "change" for st in steps):
        warnings.append(
            "plan contains EXPERIMENTAL settings without hardware validation; "
            "measure before relying on them (docs/architecture/benchmarking.md)"
        )
    return Plan(profile["name"], include_experimental, steps, warnings)
