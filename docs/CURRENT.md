# CURRENT — hot context

> Last updated: 2026-11-27 (Phase 1.4) · Keep ≤ 100 lines · Detail lives in linked docs (ADR-0002)

## Where we are

- **Phase 1.4 Packaging & bootable prototype: built and QEMU-validated.**
  - Native `.deb` `ailx`: reproducible, lintian-clean, lifecycle-tested.
  - Trust-boundary review of ADR-0011: 7 issues fixed.
  - Experimental headless reference image (Ubuntu 26.04, A/B root slots,
    data partition). It boots in QEMU/TCG, including with Secure Boot.
  - In the VM: CLI, CPU inference, A/B update, rollback and automatic fallback
    work.
- **Phase 1.3 (`ailx`) and 1.1 (`ailx-bench`): implemented, CPU-tested.**
- **Phase 1.2 (first hardware run): deferred**, because the workstation is
  unavailable. **Nothing is hardware-validated.** All GPU and hardware items
  are `hw-queued` (`state/validation-ledger.md`: VL-1.1-*, VL-1.3-*, VL-1.4-05…08).

## What exists (code)

| Area | Where |
|------|-------|
| `ailx`: status, hardware, resources, doctor, knobs, profiles, transactions, engines | `python/src/ailx/system/` |
| A/B image management (`ailx image status/stage/confirm/rollback/health`) | `system/image.py` |
| Native Debian package (build, maintainer scripts, lifecycle test) | `packaging/deb/` |
| Reference image builder + QEMU test harness | `packaging/image/` |
| Measurement foundation (`ailx-bench`), schemas, helpers, CI (incl. deb job) | `python/src/ailx/`, `schemas/`, `helpers/`, `.github/` |

Specs: `architecture/reference-image.md` · `architecture/trust-boundary-review.md`
· `architecture/runtime-foundation.md`. Results: `state/image-prototype-report.md`.

## Decisions

- Maintainer (2026-11-26): ADRs 0005–0008 and 0010 accepted; ADR-0009 MIT /
  GPL-2.0-only; Python harness; Ubuntu 26.04 + NVIDIA reference platform.
- **Proposed, need maintainer review:**
  - **ADR-0011**: privilege model, amended by the review.
  - **ADR-0012**: experimental reference image with A/B slots. This is not
    the base-distro decision of ADR-0005.

## Facts learned (QEMU/TCG and CPU only — no hardware)

- The vm image has 209 packages, 642 MiB on root, 11 running services and
  0 failed units. It idles at 326 MiB RAM used (TCG run; timings are not
  representative).
- Ubuntu's initramfs runs `/bin/sh` as init when `/sbin/init` is missing, so a
  broken slot hangs instead of panicking.
- Ubuntu blacklists watchdog drivers, and the initramfs exports
  `MODPROBE_OPTIONS=-qb`. The trial-boot watchdog must therefore load the
  driver with that variable unset. An empty value crashed kmod 34.2.
- Slots must be found by GPT partition name: writing a slot image changes its
  filesystem label.
- Journals are untrusted input, so every privileged write is re-validated.

## Next development block

1. When the workstation is available, run `runbooks/first-hardware-run.md`.
   Additionally, boot the `hw` flavor with Secure Boot and the NVIDIA driver
   (VL-1.4-05…07). Then update the ledger.
2. Without hardware:
   - maintainer review of ADR-0011 and ADR-0012;
   - signed slot manifests and a bootloader update path;
   - carry `/etc` and machine-id across slots;
   - AMD/Intel GPU knobs;
   - Phase 2 design of the self-optimizer.

## Open (maintainers)

- ADR-0011, ADR-0012; documentation and benchmark-data licence; DCO; legal
  review.
- Rule change in AGENTS.md §11: an *experimental* image prototype is allowed
  (by this phase's brief), but it is not the final distribution.

## Where to look

`INDEX.md` · `architecture/reference-image.md` · `state/image-prototype-report.md` ·
`architecture/trust-boundary-review.md` · `architecture/runtime-foundation.md` ·
`runbooks/first-hardware-run.md` · `state/validation-ledger.md`
