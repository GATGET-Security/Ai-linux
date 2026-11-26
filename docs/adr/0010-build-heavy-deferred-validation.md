# ADR-0010: Build-heavy / deferred-validation development strategy

> Status: **Accepted** (maintainer decision 2026-11-26, including the Phase 0.1 review amendments below) · Date: 2026-11-26 · Deciders: maintainers

## Context

- Much of AI Linux (kernels, images, runtime bundles for several GPU vendors)
  is expensive to build but cheap to *write*; agents and contributors often
  have no access to the target hardware.
- Performance and stability claims only mean something on real hardware
  (ADR-0003). The first hardware pool is small (one workstation, one
  Proxmox server; see `docs/architecture/hardware-test-plan.md`).
- Interleaving "write → build → test on hardware" for every change would
  serialize the whole project on two machines.

## Options considered

1. **Hardware-in-the-loop for every change** — highest confidence, does not
   scale, blocks contributors without hardware.
2. **CI-only validation** — scales, but CI runners have no representative GPUs;
   would encourage untested performance claims.
3. **Build-heavy / deferred validation** — decouple authoring and building
   from hardware validation; queue hardware validation in batches; never
   treat unvalidated work as proven.

## Decision

Adopt option 3:

1. **Authoring and building** happen continuously (agents, contributors, CI,
   build farm). Heavy artifacts (kernel builds, images, runtime bundles,
   containers) are produced reproducibly with pinned inputs and SBOMs.
2. **Validation states** per change, tracked in
   `docs/state/validation-ledger.md` (and later in the results store):
   `authored → built → unit-tested → hw-queued → hw-validated | hw-failed`.
3. **Hardware validation runs in batches** on the test pool using the
   benchmark harness (`docs/architecture/benchmarking.md`), producing result
   IDs. Batches are ordered by expected value (see
   `docs/research/07-opportunity-matrix.md`).
4. **Merge policy**: code may merge after `unit-tested`, but
   - tuning defaults, profiles and performance claims require `hw-validated`;
   - anything touching boot, drivers or kernel requires a boot-assessment test
     on at least one target before release.
5. **Release policy**: a release only contains tuning defaults whose
   validation is current for the shipped component versions (re-validation is
   triggered when a dependency version changes).

## Consequences

- Contributors without hardware remain productive; hardware time is spent on
  batches of the most valuable experiments.
- Risk of a growing backlog of unvalidated work → mitigated by WIP limits on
  `hw-queued` items and by prioritization.
- Requires a reproducible build system and artifact store early (Phase 1–3).

## Validation / revisit triggers

Revisit when the hardware pool grows (community runners) or when the
`hw-queued` backlog repeatedly exceeds one validation batch.

## Phase 0.1 review (2026-11-26)

**Recommendation:** Accept with amendment. Status changes remain a maintainer decision.

Amendments proposed by the review (`docs/research/08-architecture-review.md` §3):

1. Validation-ledger rows record the **harness version** and the **A/A
   calibration ID** used; a harness bug fix that affects metrics re-opens the
   affected rows (`hw-queued`).

## Maintainer decision (2026-11-26)

Accepted together with the Phase 0.1 review amendments above; the amendments are part of the decision.
