# ADR-0012: Experimental reference image — Ubuntu 26.04 base, A/B root slots, separate data partition

> Status: Accepted as the experimental image architecture (maintainer decision 2026-11-27); not a final base-distribution decision (ADR-0005 bake-off still decides) · Date: 2026-11-27 · Deciders: maintainers

## Context

Phase 1.4 asks for an installable, bootable minimal test system built from the
existing system layer, without pre-empting the base-distribution decision of
ADR-0005 (bake-off E-01…E-09). ADR-0008 (T3) requires image-level changes to
be reversible; ADR-0004 forbids weakening Secure Boot or other protections.
The reference platform is Ubuntu 26.04 + NVIDIA (maintainer decision).

## Options considered

1. **Package-only** (`ailx` .deb on a stock Ubuntu install) — simplest, but no
   image-level versioning or rollback.
2. **bootc/OSTree-style image** — strong model, but it is a base-distribution
   commitment (Fedora-centric tooling) that ADR-0005 defers.
3. **Plain Ubuntu rootfs in two A/B slots** (mmdebstrap, no extra update
   framework): Ubuntu's signed shim/GRUB and kernel, a GRUB environment block
   for one-time trial boots, a data partition that updates never touch.

## Decision

Option 3 as an **experimental prototype**, alongside option 1 (the .deb
remains the primary delivery, ADR-0005):

- GPT: ESP (`AILX-ESP`) · root slot A · root slot B · data (`ailx-data`:
  `/var/lib/ailx` with state, engines and models, `/home`, SSH host keys).
- Boot: Ubuntu-signed shim → GRUB → kernel (Secure Boot compatible; nothing is
  self-signed); no GRUB package in the root filesystem.
- Updates: `ailx image stage` writes a verified root filesystem image to the
  inactive slot and arms **one** trial boot; a boot service confirms it if
  health checks pass, otherwise the system reboots and GRUB falls back to the
  default slot. Kernel and initramfs panics (`panic=10`) also fall back.
  Hangs are covered by a watchdog that the initramfs arms **on trial boots
  only**: a hardware driver if present, otherwise `softdog`. systemd then takes
  it over (`RuntimeWatchdogSec`). Normal boots arm no watchdog. Ubuntu
  blacklists watchdog drivers for autoloading, and this behaviour is kept.
  `ailx image rollback` switches slots.
- Security defaults: key-only SSH, no root login, no shipped SSH host keys or
  machine-id, no passwords; unattended security upgrades stay enabled.
- Minimal package set: no documentation, man pages or locales; no snapd,
  cloud-init, desktop or telemetry packages.

## Consequences

- An image can be built, booted, upgraded and rolled back without committing
  to a final base distribution or update framework.
- In-place `apt` upgrades still work (updatability) but are slot-local; a slot
  switch discards them. The ESP bootloader is not updated by slot updates yet.
- Two root slots double the root storage (2 × 3 GiB in the VM image).

## Validation / revisit triggers

- QEMU validation results: `docs/state/image-prototype-report.md`.
- Hardware boot (Secure Boot, NVIDIA driver install, real watchdog) is
  `hw-queued`. Revisit when ADR-0005's bake-off decides the base.
