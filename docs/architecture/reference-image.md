# Reference Image Prototype (Phase 1.4): Packaging, A/B Image, Recovery

> Status: experimental prototype (ADR-0012 proposed), QEMU-validated only · Last updated: 2026-11-27

## Summary

Phase 1.4 adds two delivery forms for the existing system layer. Neither of them
decides the base distribution (ADR-0005).

1. **The native `.deb` `ailx`.** This is the primary delivery: one package on a
   stock Ubuntu 26.04. It is reproducible, lintian-clean and has a tested
   install → upgrade → remove → purge lifecycle.
2. **A headless reference image.** It is built from Ubuntu 26.04 packages plus
   that `.deb`. Root filesystems live in two A/B slots, and a separate data
   partition holds state, engines, models, `/home` and SSH host keys. Updates
   swap the whole slot, with one trial boot and automatic fallback.

Validation is limited to what was run. Results and their limits are in
`docs/state/image-prototype-report.md`; the ledger rows are VL-1.4-*.

- Hardware boot, Secure Boot on real firmware, the NVIDIA driver and a
  hardware watchdog are `hw-queued`.
- No kernel or performance tuning is part of the image.

## Separation of concerns

| Layer | Where | Changed by | Survives slot update |
|-------|-------|------------|----------------------|
| OS (kernel, userland, `ailx` package) | root slot A or B (ext4, label `ailx-rootA/B`, GPT name `ailx-rootA/B`) | `ailx image stage` (whole slot), `apt` (slot-local) | no; replaced by design |
| Bootloader (shim, GRUB, grub.cfg, grubenv) | ESP `AILX-ESP` | image build only (not yet by updates) | yes |
| AI Linux state (journal, profiles) | `/data/ailx` → bind `/var/lib/ailx` | `ailx` | yes |
| Engines | `/var/lib/ailx/engines` (data partition) | `ailx engine install/use` | yes |
| Models | `/var/lib/ailx/models` (data partition) | the user | yes; never purged |
| User data | `/data/home` → bind `/home` | users | yes |
| Host identity | `/data/ssh` (SSH host keys, generated at first boot) | `ailx-firstboot` | yes |

The engines stay on the data partition, so a slot update does not rebuild
llama.cpp. Each engine's manifest records the toolchain it was built with.
`ailx engine verify` detects tampering, but it does **not** detect ABI drift
after a base update. That is a known limit: re-install the engine after a major
base change.

## Package (`packaging/deb/`)

- **Layout.** Code goes to `/usr/lib/python3/dist-packages/ailx` (root-owned).
  The entry points `/usr/bin/ailx` and `ailx-bench` use
  `#!/usr/bin/python3 -I` (isolated mode, trust-boundary review F4). Helpers
  go to `/usr/libexec/ailx/`. The package also ships a systemd unit for the
  RAPL collector, `sysusers.d` (group `ailx`), `tmpfiles.d` (`/var/lib/ailx`
  2770, engines/models 2775, root:ailx), the conffile `/etc/ailx/ailx.toml`,
  man pages and the workloads.
- **Dependencies.** Ubuntu 26.04 archive packages only: `python3-numpy`,
  `-scipy`, `-jsonschema`, `-zstandard`, `-rfc8785`, plus `systemd` and `sudo`.
  The engine build tools are `Recommends`; the NVIDIA utilities are `Suggests`.
- **Maintainer scripts.**
  - `postinst`: group, tmpfiles, byte-compile, `try-restart`.
  - `prerm`: rolls back an active profile on remove and stops the collector.
  - `postrm purge`: removes state, engines and the sudoers file written by
    ailx, and **keeps models**.
  - The sudoers rule stays opt-in: `ailx system sudoers enable`.
  - `ailx system install` refuses to run on a dpkg-managed layer.
- **Reproducible.** File mtimes are clamped to `SOURCE_DATE_EPOCH` (the git
  commit time), gzip stores mtime 0, and the tar uses `--root-owner-group`. Two
  builds are byte-identical; CI checks this.

## Image (`packaging/image/build_image.py`)

```
GPT:  ESP 128 MiB (AILX-ESP) │ rootA 3 GiB │ rootB 3 GiB │ data (ailx-data)
boot: UEFI → shim (Microsoft-signed, from Ubuntu) → GRUB (Canonical-signed) → kernel (Canonical-signed)
```

- **Build.**
  - `mmdebstrap --variant=apt` builds the rootfs. Filesystems are made by
    `mke2fs -d` and `mkfs.vfat` + `mtools`, and the GPT by `sfdisk`. No loop
    devices or mounts are needed.
  - Timestamps come from `SOURCE_DATE_EPOCH`. Filesystem UUIDs and hash seeds
    are derived from the build ID.
  - A manifest lists every package (name, version, size) and the sha256 of
    every artifact.
  - Byte-identical rebuilds additionally need a pinned snapshot mirror
    (`--mirror https://snapshot.ubuntu.com/…`). This is **not verified**: the
    snapshot service was unusable from the build sandbox.
- **Flavors.**
  - `vm`: `linux-image-virtual`.
  - `hw`: `linux-image-generic`, `linux-firmware`, `ubuntu-drivers-common`.
    The proprietary NVIDIA driver is not shipped; `ubuntu-drivers` installs it
    on the target.
- **Minimal set.** About 210 packages (vm). Documentation, man pages, locales,
  snapd, cloud-init, desktop and telemetry packages are left out. These are
  kept because the guardrails need them: OpenSSH, sudo, apt +
  unattended-upgrades (security updates), systemd-networkd/resolved/timesyncd
  (network, DNS, time), and ca-certificates.
- **Security defaults.**
  - Key-only SSH, no root login, `PasswordAuthentication no`.
  - The image ships no SSH host keys, machine-id or passwords.
  - Admin user `ailx-admin` (locked password; groups `sudo` and `ailx`).
  - The ESP is mounted `umask=0077`.
  - Secure Boot compatible, because nothing is self-signed. No security
    feature is disabled.
- **Hang and panic protection** for trial boots: `panic=10` on the kernel
  command line. The trial-boot watchdog is described below. Normal boots keep
  Ubuntu's behaviour, with no watchdog armed.

## Versioning, upgrade and recovery

- **Version identity.** `/etc/ailx/image-release` holds `VERSION`, `BUILD_ID`
  (`<version>-<git>[+dirty]-<epoch>`) and `AILX_PACKAGE`. The manifest holds
  the package list and hashes, and `ailx --version` shows the package version.
- **Upgrade (A/B).**
  1. `sudo ailx image stage <rootfs.ext4|device> --sha256 <manifest sha>` finds
     the inactive slot by its **GPT partition name**. It resolves the device
     once and refuses a source that carries the running slot's label.
  2. It copies and hashes the image, runs `e2fsck -fn`, sets the slot label,
     and only then sets `next_slot` in grubenv.
  3. GRUB boots the next slot **once**: it clears `next_slot` and records
     `trial_result=pending` before booting.
  4. `ailx-boot-health.service` runs on trial boots. Its checks are: doctor
     shows no `fail`, there are no failed units, and the data partition is
     mounted. If they pass, it sets `default_slot`. Otherwise it records the
     failure and reboots into the old default.
  5. A panic reboots after 10 s. Either way, GRUB falls back because
     `next_slot` is already cleared.
  6. A hang triggers the **trial-boot watchdog**. On `ailx.trial=1`, an
     initramfs `init-bottom` script loads a hardware watchdog driver
     (`i6300esb`, `iTCO_wdt`, `sp5100_tco`, `wdat_wdt`), or `softdog` with a
     120 s margin if none is present. It writes to `/dev/watchdog0` without the
     magic close, so the watchdog keeps running. systemd takes it over through
     `RuntimeWatchdogSec=2min`. If the slot never starts systemd, the machine
     resets and the previous slot boots. The QEMU validation found this case:
     without a working `/sbin/init`, Ubuntu's initramfs runs `/bin/sh` as init
     and would otherwise hang forever.
- **Rollback.** `sudo ailx image rollback` sets the other slot as default
  (after a reboot). The GRUB menu also offers both slots.
- **In-place updates.** `apt upgrade` works for security fixes, but it is
  slot-local: a later slot switch discards it. The release process is meant to
  publish new slot images.
- **Data never moves.** Updates never write the data partition.

## Testing (`packaging/image/vm_test.py`)

QEMU q35 with OVMF (optionally Secure Boot with Microsoft keys), using KVM when
`/dev/kvm` exists and TCG otherwise. The test uses an i6300esb watchdog, a
copy-on-write overlay, and update and broken-rootfs images attached as extra
disks. The scenario is:

1. boot, inventory and metrics;
2. CLI, doctor, engine verification, CPU inference and a simulated A/A
   campaign;
3. profile apply and rollback;
4. stage the update → trial boot → health confirmation, then check that data
   persisted;
5. rollback;
6. stage an unbootable rootfs → automatic fallback.

Each step's command, exit code and output go to `report.json`.

## ISO

Not built. A live/installer ISO would need an installer: partitioning a target
disk, writing the slots and installing the bootloader. That is a separate
product decision tied to ADR-0005. For VMs, the qcow2 is the natural artifact.
For bare metal, the raw disk image can be written to a disk with `dd`.

## Limits

- Prototype only; ADR-0012 is proposed.
- Not hardware-validated: VL-1.4-05 … 08 are `hw-queued`.
- **Slot-local state.** Each slot has its own machine-id, apt state and
  `/etc`. Configuration edits in `/etc` do not carry across slot updates yet.
- **Bootloader updates.** Slot updates do not update the ESP (shim, GRUB). A
  signed bootloader update path is still open.
- **Shared labels.** Two disks with AI Linux images in the same machine would
  share labels and partition names. Disambiguation (by disk ID) is not
  implemented.
- **No update server.** There is no update channel or signature on slot images
  beyond the sha256 from the manifest. Signed manifests are an open item for
  the release process.
