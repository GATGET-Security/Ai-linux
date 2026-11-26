# CURRENT — hot context

> Last updated: 2026-11-26 (Phase 1.1) · Keep ≤ 100 lines · Detail lives in linked docs (ADR-0002)

## Where we are

- **Phase 1.1 Measurement Foundation: implemented (build-heavy), unit-tested in
  CPU-only CI, hardware validation pending.** No measurement on the reference
  hardware exists yet; every hardware-dependent item is `hw-queued`
  (`state/validation-ledger.md`).
- Phase 0/0.1 research and architecture: complete (`INDEX.md`).

## What exists (code)

| Area | Where |
|------|-------|
| Canonical JSON Schemas v0 + conformance corpus | `schemas/` |
| `ailx-bench` (probe, runner, adapters llama-bench / vLLM / synthetic, RMIT + BCa + MWU + BH-FDR + power, A/A calibration, telemetry PSI/CPU/NVML/RAPL, KLD gate, content-addressed store, reports, claim validator) | `python/` |
| Minimal privileged RAPL helper | `helpers/rapl-collector/` |
| Smoke workloads, campaign specs, hardware templates (A/A, E1 power limit) | `workloads/` |
| CI (lint, mypy strict, tests, SBOM, pinned llama.cpp e2e) | `.github/workflows/ci.yml` |
| First hardware run | `runbooks/first-hardware-run.md` |

## Decisions (maintainer, 2026-11-26)

- ADRs 0005–0008 and 0010 **accepted** incl. review amendments.
- ADR-0009 amended + accepted: own components **MIT**; kernel/GPL-derived
  **GPL-2.0-only** where required; third-party unchanged; legal review before
  public release still open; docs licence still open.
- Python for benchmark infrastructure; contracts stay language-neutral.
- Reference platform: **Ubuntu 26.04 + NVIDIA**. Wall meter is not a blocker.

## Facts learned while implementing (simulation/CI only)

- Noise floor = |A/A point estimate|; the CI-bound variant made a real 5%
  effect undetectable (spec §8, deviation 1).
- Power analysis needs a Monte-Carlo margin to avoid under-sized campaigns
  (deviation 2).
- Verdict `no-detectable-difference` is never equivalence; equivalence only
  with a pre-registered margin.

## Key hypotheses next in line (untested)

H-S-03 (A/A on hardware), H-S-01 (power limit / energy), H-H-08/H-S-07
(NVML accuracy on Ada), H-H-09 (Proxmox CPU type) — see `state/hypotheses.md`.

## Next development block: Phase 1.2 "First hardware run"

Execute `runbooks/first-hardware-run.md` on the workstation and the Proxmox
server: HW-0 inventory → A/A calibration → E1 power limit → ledger update.
Then: replace the constructed vLLM fixture with a captured one (VL-1.1-03),
and start the Ubuntu baseline U0/U1/U2 campaigns (`architecture/benchmarking.md`).

## Open (maintainers)

- Documentation and benchmark-data licence; DCO enforcement; legal review.
- Sudoers rule for operator hooks (E1) is an operator decision per machine.

## Where to look

`INDEX.md` · `architecture/phase1.1-measurement-foundation.md` (§8 status) ·
`runbooks/first-hardware-run.md` · `state/validation-ledger.md` ·
`architecture/benchmarking.md` · `architecture/hardware-test-plan.md`
