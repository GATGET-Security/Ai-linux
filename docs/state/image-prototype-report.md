# Reference Image Prototype — QEMU Validation Report (Phase 1.4)

> Status: measured in QEMU/TCG on the cloud build host · Last updated: 2026-11-27

## Summary

The reference image was built, booted and exercised in an emulated VM: QEMU 8.2.2,
**TCG** (no KVM), q35, OVMF. Everything below happened in that VM.

| Item | Result |
|------|--------|
| Bootable image produced | ✅ yes |
| Boot chain | ✅ UEFI → shim → GRUB → kernel 7.0.0-44 |
| Secure Boot | ✅ with OVMF + Microsoft keys: `SecureBoot=1`, `lockdown=integrity` |
| CLI, doctor, engine verify | ✅ work in the image |
| CPU inference | ✅ llama-bench on a random-weight test model |
| Measurement harness | ✅ simulated A/A campaign |
| Profile apply/rollback | ✅ also works under lockdown |
| A/B update | ✅ sha256-verified stage → trial boot → health confirm |
| Data persistence | ✅ models, `/home` and SSH host key survived |
| Rollback | ✅ |
| Fallback from an unbootable slot | ✅ automatic, after the fixes below |

The four VM runs found **four real defects**. All are fixed, and the final run
(run 4) passed every step.

**Nothing here is a hardware result.** TCG emulates the CPU, so all timings are
unrepresentative of any real machine and must not be compared with
anything. Hardware rows VL-1.4-05 … 08 stay `hw-queued`.

## Artifacts (run 4, build ID `<version>-5e31384da079+dirty-1795785247`)

`+dirty` means the images were built from the Phase 1.4 working tree on top
of commit 5e31384, before that tree was committed.

| Artifact | Size | sha256 (prefix) |
|----------|------|------------------|
| `ailx-1-vm.raw` (GPT, 8.1 GiB virtual) | 8,726,249,472 B, 1.39 GiB allocated | `323701b944af…` |
| `ailx-1-vm.qcow2` (compressed) | 767,295,488 B (≈ 732 MiB) | `5edc31bd735e…` |
| `rootfs-1-vm.ext4` (slot image, 3 GiB) | 731 MB allocated | `85e0d9f61443…` |
| `rootfs-2-vm.ext4` (update image, `ailx` 0.1.1-1) | 731 MB allocated | `35fe3db31fd5…` |
| `rootfs-broken.ext4` (v2 without `/usr/lib/systemd/systemd`) | — | `e22f86129f2d…` |
| `ailx_0.1.0-1_all.deb` / `ailx_0.1.1-1_all.deb` | ≈ 78 KiB | `5530cbd27774…` / `aab84b914893…` |

- The qcow2 includes the data preload: llama.cpp engine 46 MB plus 25 MB of
  test models. The images were **not** committed: they are binaries.
- **`hw` flavor (rootfs only, not booted).** 248 packages, 1,289 MiB used,
  including `linux-image-generic`, `linux-firmware*` (incl.
  `linux-firmware-nvidia-graphics`) and `ubuntu-drivers-common`. It needs a
  root slot larger than 3 GiB for headroom.

## Measured in the VM (run 4, TCG, 4 vCPUs, 3 GiB RAM)

| Metric | Value | How |
|--------|-------|-----|
| Packages installed | 209 | `dpkg-query -W \| wc -l` |
| Root filesystem used | 642 MiB (`df`), 573 MiB apparent | `df -m /` |
| Boot to SSH (first boot) | 53.4 s | host side, QEMU start → SSH login works |
| `systemd-analyze` | 12.2 s kernel + 26.5 s userspace = 38.7 s | in the guest |
| Idle RAM | 326 MiB used, 2,568 MiB available of 2,895 MiB | `free -m`, 60 s after boot finished |
| Running services | 11 | dbus, getty×2, ssh, journald, logind, networkd, resolved, timesyncd, udevd, unattended-upgrades |
| Timers | 7 | tmpfiles-clean, apt-daily, apt-daily-upgrade, motd-news, dpkg-db-backup, e2scrub_all, fstrim |
| Failed units | 0 | `systemctl --failed` |
| Trial boot → SSH / rollback boot → SSH | 64.0 s / 59.7 s | host side |
| Broken trial → fallback slot reachable | 142.7 s wall | reboot → trial hang → watchdog reset → default slot |
| Secure Boot run: boot to SSH | 70.8 s (2 vCPUs) | `--secureboot` |

### Function checks inside the image

- **`ailx doctor`.** Exit 0. `sched_ext` is available, the engine integrity
  check passes, and the helper is usable through `sudo -n`. It warns that
  `spec_store_bypass` is vulnerable. That is the emulated CPU model; AI Linux
  never disables mitigations.
- **CPU inference.** `llama-bench` from the engine on the data partition ran on
  the 8.5 M-parameter **random-weight test model** (`tools/make_tiny_gguf.py`).
  The numbers (pp32 ≈ 23 t/s, tg16 ≈ 2 t/s) only show that inference executes
  under TCG. They are not performance data.
- **`ailx-bench`.** The simulated A/A campaign gives `no-detectable-difference`
  and the store verifies. The output is labeled `SIMULATED DATA`.
- **Profile.** `ailx profile apply inference --include-experimental` set
  swappiness 60 → 10. It was verified and then rolled back to 60. This also
  worked with Secure Boot lockdown active.
- **SSH.**
  - The effective sshd configuration has `permitrootlogin no`,
    `passwordauthentication no` and `kbdinteractiveauthentication no`.
  - The host keys live in `/data/ssh`. They were generated at first boot and
    were unchanged after the A/B update.
- **Watchdog on normal boots.** There is no `/dev/watchdog*` on normal boots,
  as designed. On trial boots, `i6300ESB timer` is armed by the initramfs and
  taken over by systemd (timeout 120 s).

### A/B scenario (run 4)

1. `sudo ailx image stage /dev/vdb --sha256 35fe3db…` wrote slot B and verified
   the hash, then armed one trial boot.
2. Reboot. GRUB logged `booting slot B (trial=1)`. The health service then
   reported:

   ```
   {"trial": true, "confirmed": true, "checks": [["doctor-no-fail", true], ["no-failed-units", true], ["data-partition", true]]}
   ```

   The default slot became B (`VERSION="2"`, `ailx` package 0.1.1-1). The model
   marker, the home marker and the SSH host key were kept, and the engine
   verified.
3. `sudo ailx image rollback`, then reboot: slot A (`VERSION="1"`).
4. Staged the broken rootfs and rebooted. GRUB booted slot B with
   `trial=1`, and the initramfs armed the watchdog. The initramfs found no
   `/sbin/init`, so it ran `/bin/sh` as init and hung. The watchdog then reset
   the VM, and GRUB booted slot A. `ailx image status` reported:

   ```
   last trial: failed (did not complete; fell back to slot A)
   ```

## Findings (defects found by the VM runs, all fixed)

| # | Run | Defect | Fix |
|---|-----|--------|-----|
| B1 | 1 | `ailx image status` run as a normal user said "not an image": the ESP is mounted `umask=0077`, so `grubenv` is unreadable | Detect the image from the kernel command line and report partial status with a note. Changing the boot environment requires root. Unit test added. |
| B2 | 1 | `ailx image stage` corrupted the slot lookup. The update image carries the label `ailx-update`, so after the copy `/dev/disk/by-label/ailx-rootB` vanished. `e2fsck` failed (exit 8), and the slot could no longer be found. | Find slots by **GPT partition name**, resolve the device once, refuse sources carrying the running slot's label, and turn tool errors into clear messages. Regression test with real ext4 images added. |
| B3 | 2 | A slot without working systemd **hung forever**. Ubuntu's initramfs falls back to `/bin/sh` as init, and no watchdog was ever armed: systemd never ran, and Ubuntu blacklists watchdog drivers for autoloading. | An initramfs `init-bottom` script arms a watchdog on **trial boots only**, using a hardware driver or `softdog` as fallback. systemd takes it over. |
| B4 | 3 | The B3 fix did not load the driver. The initramfs exports `MODPROBE_OPTIONS=-qb`, and `-b` applies the blacklist even to explicitly named modules. An *empty* `MODPROBE_OPTIONS` crashed kmod 34.2 ("buffer overflow detected"). | Call `modprobe` in a subshell with `MODPROBE_OPTIONS` unset. Before rebuilding, this was verified interactively in the initramfs: after arming, the VM reset within the 30 s heartbeat. |

Two test-harness defects were also fixed:

- The VM test continued after a failed stage step instead of aborting the
  scenario.
- The serial markers were polluted by ANSI escape sequences.

## Limits of this validation

- **TCG only.** There is no KVM on the build host. Timings, idle CPU and
  inference speed are not representative.
- **Firmware and watchdog are emulated.** OVMF 2024.02 with the Microsoft
  keys from the host's `ovmf` package (`OVMF_VARS_4M.ms.fd`). Real firmware may
  differ (VL-1.4-05). Only the emulated i6300esb watchdog was exercised
  (VL-1.4-07).
- **Not covered.** The `hw` flavor was built but not booted. The NVIDIA
  driver, a GPU, Proxmox/KVM and the unattended-upgrades runtime behaviour were
  not tested.
- **Not attempted.** A kernel *panic* fallback was not exercised; only the hang
  path was. The panic path relies on `panic=10` and the initramfs `panic()`
  reboot, which were read in source but not run.
- **Reproducibility.** Rebuilding the images is reproducible in structure (same
  package set, fixed UUIDs and timestamps). Byte identity was not verified: no
  pinned snapshot mirror was used. The `.deb` itself is byte-identical across
  rebuilds.

## Reproduce

```bash
python3 packaging/deb/build_deb.py --version 0.1.0-1 --out out/
sudo python3 packaging/image/build_image.py --deb out/ailx_0.1.0-1_all.deb --version 1 \
     --authorized-key ~/.ssh/id_ed25519.pub [--data-preload DIR] --out out/
sudo python3 packaging/image/build_image.py --deb out/ailx_0.1.1-1_all.deb --version 2 --rootfs-only --out out/
python3 packaging/image/vm_test.py --disk out/ailx-1-vm.qcow2 --key ~/.ssh/id_ed25519 \
     --update out/rootfs-2-vm.ext4 --update-sha256 <manifest sha> --broken out/rootfs-broken.ext4 --out report/
python3 packaging/image/vm_test.py --disk out/ailx-1-vm.qcow2 --key ~/.ssh/id_ed25519 --secureboot --quick --out report-sb/
```
