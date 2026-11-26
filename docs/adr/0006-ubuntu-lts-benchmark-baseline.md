# ADR-0006: Ubuntu LTS as the benchmark baseline

> Status: Proposed · Date: 2026-11-26 · Deciders: maintainers (pending; baseline choice given in the project brief)

## Context

The brief specifies Ubuntu as the baseline. Ubuntu LTS is the most common
target of AI tooling documentation, ships CUDA (multiverse, under a
redistribution agreement) and ROCm in its archive, and provides
Canonical-signed NVIDIA kernel modules [D-12][D-13][D-15][D-47][L-12][L-15].
The current LTS is 26.04 "Resolute Raccoon" with Linux 7.0 [D-02]. A baseline
must be precisely defined to be reproducible.

## Decision (proposed)

1. **Baseline = current Ubuntu LTS, stock** (26.04 LTS at the time of writing):
   default installer choices, archive kernel (`generic` flavour), NVIDIA
   driver as recommended by `ubuntu-drivers` from the archive, no tuning.
   Automatic updates are frozen during measurement campaigns; all versions are
   recorded in the Environment Fingerprint.
2. **Runtime bundles are identical across all arms** (same OCI image digests or
   same binaries, same model files by hash). Models are read from a dedicated
   model partition formatted identically for all arms (ext4 default), except in
   experiments where the file system is the variable.
3. **Comparison arms** U0/U1/U2/A as defined in
   `docs/architecture/benchmarking.md` §1; headline claims compare A vs U2.
4. **Baseline refresh**: when a new Ubuntu LTS or a point release with a new
   HWE kernel ships, the baseline is re-measured; old results stay valid for
   their fingerprint but are not mixed with new ones.

## Consequences

- Claims are relative to what most users actually run.
- Containerized runtime bundles add a small, measurable layer (overlay file
  systems, cgroups) — mitigated by bind-mounting model files and by one
  native (non-container) control run per campaign.

## Validation / revisit triggers

Revisit if most target users move to another distribution, or if the
container layer itself is shown to distort results (control run disagrees
beyond the A/A noise floor).

## Phase 0.1 review (2026-11-26)

**Recommendation:** Accept with amendments. Status changes remain a maintainer decision.

Amendments proposed by the review (`docs/research/08-architecture-review.md` §3):

1. The baseline kernel is the **GA kernel** of the LTS (26.04: Linux 7.0
   [D-02]). An HWE kernel is a **new baseline generation**, because HWE
   kernels ≥ 7.2 bring `SCHED_CACHE` (default on, multi-LLC CPUs) into
   "stock" Ubuntu [V-01][V-02].
2. The fingerprint records active tuning daemons (power-profiles-daemon,
   TuneD, irqbalance, thermald) in every arm; arm U2 replaces the stock power
   daemon by TuneD explicitly.
