# ADR-0005: Layer-first delivery; base distribution for an own image decided by a measured bake-off

> Status: Proposed · Date: 2026-11-26 · Deciders: maintainers (pending)

## Context

The brief asks to compare Debian, Arch, CachyOS, Fedora, NixOS, Buildroot and
Yocto as foundations without deciding prematurely. Research
(`docs/research/05-base-distributions-and-licensing.md`) found:

- No distribution dominates; with default weights Ubuntu 26.04 LTS scores
  highest *as a host* (≈77), Debian and Fedora bootc tie (≈69), NixOS ≈66,
  CachyOS/Arch/Yocto ≈55–58, Buildroot ≈48, each ±10 points. With
  immutability-first weights, Fedora bootc and Ubuntu are level [D-*, §1.7–1.8].
- The GPU stack (driver packaging, signed modules, CUDA/ROCm availability)
  separates distributions more than the kernel does [D-12][D-13][D-15][D-35][D-43][D-72].
- Whether OS-level tuning matters at all for local AI is unproven
  (H-L-01, H-D-01, H-D-03); no primary benchmark data exists.
- Building and maintaining an own image carries release-engineering, CVE and
  (likely) EU CRA steward duties [D-89].

The project's value — measured, quality-gated, reversible optimization — does
not require an own image to be demonstrated, but some optimizations (kernel
config, signed modules, atomic rollback of the whole stack) eventually do.

## Options considered

1. **Pick a base now and build an own image** — premature; no data; highest
   effort before any value is shown.
2. **Layer-first (strategy a+b)**: ship AI Linux as a distro-agnostic layer
   (CLI, daemon, systemd units, sysext/confext where useful, OCI runtime
   bundles, BPF schedulers) on existing distributions; defer the own image.
3. **Never build an image** — cannot deliver kernel-, driver- and
   rollback-level guarantees.

## Decision (proposed)

1. **Phases 1–2: layer mode.** Primary development and baseline host:
   Ubuntu 26.04 LTS (identical to the benchmark baseline, ADR-0006). Secondary
   test hosts: Fedora 45 (incl. bootc), Debian 13, Arch/CachyOS, NixOS.
   Runtime bundles are OCI images pinned by digest; host drivers come from the
   host distribution.
2. **Own-image prototypes in CI only** (no user-facing image before Phase 3):
   primary candidates **Fedora bootc** and **Debian + mkosi**; an
   **Ubuntu + mkosi** image serves as a control (inherits Canonical-signed
   NVIDIA modules). NixOS remains a candidate for reproducible runtime layers.
   Buildroot and Yocto are reserved for a possible later appliance/edge track.
3. **Decision gate**: the base for the own image is chosen in a new ADR after
   the deciding experiments E-01…E-09 in
   `docs/research/05-base-distributions-and-licensing.md` §1.9 (ISA builds,
   kernel/scheduler, version lag, module signing, rollback drills, maintenance
   effort, reproducibility, sysext portability, SBOM/licence review), run with
   the benchmark methodology of `docs/architecture/benchmarking.md`.

## Consequences

- Value can be shown (or disproven) on mainstream distributions before the
  heavy investment of an own image.
- Users can adopt AI Linux without reinstalling their OS.
- Kernel-level and boot-level features (custom kernel config, signed
  out-of-tree modules, full-stack rollback) are limited in layer mode; they are
  explored in CI image prototypes and on dedicated test installs.
- The support matrix of host distributions must stay small (Ubuntu LTS first).

## Validation / revisit triggers

- If Phase 1–2 measurements show that the largest gains need kernel/boot-level
  control, accelerate the image track.
- If measurements show OS-level gains are negligible (H-L-01 confirmed), keep
  layer mode as the main product and treat the image as a convenience.
