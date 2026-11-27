# CURRENT — hot context

> Last updated: 2026-11-27 (Phase 1.3) · Keep ≤ 100 lines · Detail lives in linked docs (ADR-0002)

## Where we are

- **Phase 1.3 Runtime Foundation: implemented (build-heavy), CPU-integration-
  tested.** `ailx` system layer exists next to the `ailx-bench` measurement
  foundation (Phase 1.1).
- **Phase 1.2 (first hardware run) deferred** — workstation unavailable. No
  measurement on the reference hardware exists; every GPU/hardware-dependent
  item is `hw-queued` (`state/validation-ledger.md`, VL-1.1-*, VL-1.3-*).

## What exists (code)

| Area | Where |
|------|-------|
| `ailx`: status, hardware, resources, doctor, knobs | `python/src/ailx/system/` |
| Profiles baseline / inference / training / energy-efficiency (all settings experimental, inactive by default) | `system/data/profiles/` |
| Transactions: journal, verify, rollback, dead-man confirm, drift, crash recovery | `system/transactions.py` |
| Privileged apply helper (own allow-list, sudo -n) | `helpers/apply-helper/` |
| Engines: llama.cpp (pinned 98cc730, static builds), vLLM 0.34.0 (isolated venv), manifests + integrity | `system/engines.py` |
| System install/uninstall (layer mode, install manifest) | `system/install.py` |
| Measurement foundation (`ailx-bench`), schemas, RAPL collector, CI | `python/src/ailx/`, `schemas/`, `helpers/`, `.github/` |

Spec and limits: `architecture/runtime-foundation.md` · ADR-0011 (proposed).

## Decisions

- Maintainer (2026-11-26): ADRs 0005–0008, 0010 accepted; ADR-0009 MIT /
  GPL-2.0-only; Python harness; Ubuntu 26.04 + NVIDIA reference platform.
- **Proposed, needs maintainer review: ADR-0011** (unprivileged planner +
  allow-listed privileged helper + journaled transactions; runtime-only
  changes; no boot persistence before validation).

## Facts learned while implementing (CPU/cloud only)

- Plans can be stale at apply time (e.g. `--replace`); the transaction now
  re-reads current values under the lock — a real bug caught by tests.
- Live apply → verify → rollback and dead-man auto-rollback worked on the
  cloud container (THP, swappiness); cpufreq/X3D/NVIDIA paths only on fakes.
- llama.cpp CPU engine build at the pinned commit: static, relocatable,
  manifest + tamper detection verified (cloud host).

## Next development block

1. When the workstation is available: Phase 1.2 runbook
   (`runbooks/first-hardware-run.md`, now using `ailx system install` and
   `ailx engine install llama.cpp --variant cuda`), then ledger updates.
2. Without hardware: review/accept ADR-0011; `.deb` packaging of the layer;
   AMD/Intel GPU knobs (detection exists, control does not); Phase 2 design
   of the self-optimizer on top of transactions + campaigns.

## Open (maintainers)

- ADR-0011; documentation and benchmark-data licence; DCO; legal review.
- Sudoers drop-in is opt-in per machine (`ailx system install --with-sudoers`).

## Where to look

`INDEX.md` · `architecture/runtime-foundation.md` ·
`architecture/phase1.1-measurement-foundation.md` · `runbooks/first-hardware-run.md` ·
`state/validation-ledger.md` · `adr/0011-system-layer-privilege-and-transactions.md`
