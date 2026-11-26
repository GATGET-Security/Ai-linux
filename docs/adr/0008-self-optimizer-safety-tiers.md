# ADR-0008: Self-optimizer safety tiers and adoption rules

> Status: Proposed · Date: 2026-11-26 · Deciders: maintainers (pending)

## Context

The brief requires a system that benchmarks configurations, scores them, adopts
winners and rolls back regressions. Tuning knobs differ in blast radius
(process restart vs. unbootable system), reversibility, security impact and
quality impact. Benchmark noise can masquerade as improvement. Evidence on
auto-tuning, rollback mechanisms and rigorous benchmarking:
`docs/research/06-self-optimization-and-benchmarking.md`.

## Options considered

1. **Static expert profiles only** (TuneD-style) — safe, but no adaptation to
   the actual machine/workload.
2. **Unconstrained auto-tuning** (any knob, any search) — maximal exploration;
   unsafe; would drift towards security- or quality-reducing settings.
3. **Registry-constrained, tiered auto-tuning with statistical adoption rules
   and automatic rollback.**

## Decision (proposed)

Option 3, as specified in `docs/architecture/self-optimization.md`:

- Only knobs from the **knob registry** may be changed; each knob carries
  `security_class`, `quality_class`, reversibility and preconditions.
- **Tiers**: T0 process config, T1 runtime system (dead-man auto-revert),
  T2 boot (new boot entry + automatic boot assessment), T3 image (generation
  rollback). The optimizer adopts autonomously only in T0/T1 and only
  `allowed` + lossless knobs; T2 needs user confirmation by default; T3 is a
  release-process decision.
- **Adoption** requires: CI lower bound above a calibrated minimum effect,
  no guard-metric regression, quality gate pass, stability gate pass,
  replication.
- **Regression watch** with canary benchmarks and change-point detection;
  automatic rollback to the previous generation.

## Consequences

- Slower than unconstrained tuning; much lower risk.
- Requires a calibrated noise model per machine (A/A tests) before any
  adoption is possible — this is part of the Phase 1 measurement foundation.

## Validation / revisit triggers

Phase 2 experiment: inject known-neutral and known-harmful knob changes and
verify that the rules reject them at the configured false-positive rate.

## Phase 0.1 review (2026-11-26)

**Recommendation:** Accept after amendments. Status changes remain a maintainer decision.

Amendments proposed by the review (`docs/research/08-architecture-review.md` §3):

1. Screening campaigns use FDR control (Benjamini–Hochberg); adoption only
   after a pre-registered confirmatory campaign with fresh data.
2. **Probation**: T1 adoptions become persistent only after a soak subset
   without stability events.
3. **Coexistence**: knobs managed by other daemons are taken over only with
   explicit consent, otherwise left alone.
4. Knobs that require lockdown to be off (debugfs/MSR writes, e.g.
   `SCHED_CACHE` tunables [V-01]) are `explicit-opt-in` and never auto-applied.
Details: `docs/architecture/self-optimization.md`.
