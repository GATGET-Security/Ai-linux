# Validation Ledger (deferred validation)

> Status: active · Last updated: 2026-11-27 · Policy: ADR-0010 (accepted)

States: `authored → built → unit-tested → hw-queued → hw-validated | hw-failed`.
Rows record the harness version and A/A calibration ID once hardware runs exist
(ADR-0010 amendment). Rows become stale (`hw-queued`) when a dependency in the
fingerprint changes.

| ID | Change / claim | Linked hypothesis | Target hardware | State | Result / analysis IDs | Harness | Notes |
|----|----------------|-------------------|-----------------|-------|------------------------|---------|-------|
| VL-1.1-01 | Probe reads topology, GPU, storage, security and scheduler state correctly on the reference workstation (2 LLCs 96/32 MiB, RTX 4090 Gen4 x16, lockdown, X3D mode) | — | Workstation | hw-queued | — | 0.1.0 | Fixture tests only (constructed sysfs trees) |
| VL-1.1-02 | NVML sampler: energy counter, power, clocks, throttle reasons on Ada GeForce | H-S-07, H-H-08 | Workstation | hw-queued | — | 0.1.0 | Not runnable in CI (no GPU) |
| VL-1.1-03 | vLLM adapter against a real `vllm bench serve` result (replace constructed fixture) | — | Workstation | hw-queued | — | 0.1.0 | Fixture is constructed, see `python/tests/fixtures/README.md` |
| VL-1.1-04 | RAPL helper on Zen 4 (zones, rate limit, group access) | — | Workstation | hw-queued | — | 0.1.0 | Tested with a fake powercap tree |
| VL-1.1-05 | A/A calibration on real hardware: no false claims, noise floor and run-level CV per metric | H-S-03 | Workstation | hw-queued | — | 0.1.0 | Runbook §4 |
| VL-1.1-06 | E1 GPU power limit 70% vs default | H-S-01 | Workstation | hw-queued | — | 0.1.0 | Runbook §5 |
| VL-1.1-07 | Proxmox VM CPU type `host` exposes AVX-512; A/A on the VM | H-H-09 | Proxmox server | hw-queued | — | 0.1.0 | Runbook §7 |
| VL-1.1-08 | Statistics, campaigns, store, reports, KLD gate | — | CI (CPU) | unit-tested | CI run on the PR | 0.1.0 | Simulated data and random-weight test model only |
| VL-1.3-01 | Profile transactions (plan, journal, verify, rollback, dead-man, drift, crash recovery) | — | Cloud host (live THP/swappiness) + fake roots | unit-tested | CI run on the PR | 0.1.0 | Live apply/rollback/watchdog executed on the Ubuntu 24.04 cloud container (kernel 6.18); not the reference host |
| VL-1.3-02 | Privileged helper via real `sudo -n` with the installed sudoers drop-in | — | Workstation (Ubuntu 26.04) | hw-queued | — | 0.1.0 | Tested without sudo (helper process + test root) |
| VL-1.3-03 | cpufreq EPP/governor and X3D-mode knobs on real Zen 4 sysfs | H-K-09 | Workstation | hw-queued | — | 0.1.0 | Cloud host exposes no cpufreq/X3D interfaces; fake sysfs only |
| VL-1.3-04 | NVIDIA power-limit knob (read default, reduce, restore) | H-S-01 | Workstation | hw-queued | — | 0.1.0 | Fake nvidia-smi only |
| VL-1.3-05 | `ailx resources` live VRAM via NVML | — | Workstation | hw-queued | — | 0.1.0 | No GPU in CI |
| VL-1.3-06 | llama.cpp engine: CPU variant build, manifest, verify, tamper detection | — | Cloud host | unit-tested | — | 0.1.0 | Real 282 s build at commit 98cc730 (static, 5 targets) |
| VL-1.3-07 | llama.cpp engine: CUDA variant build and smoke test | — | Workstation | hw-queued | — | 0.1.0 | Needs CUDA toolkit + GPU |
| VL-1.3-08 | vLLM 0.34.0 engine install (CUDA) and `vllm --version` smoke | — | Workstation | hw-queued | — | 0.1.0 | pip engine mechanism tested with a stand-in package (six 1.17.0) from PyPI |
| VL-1.3-09 | System install/uninstall on the real host (group, sudoers, symlinks) | — | Workstation / Proxmox VM | hw-queued | — | 0.1.0 | Tested into isolated roots only |
| VL-1.3-10 | Profile comparison campaign (stock vs inference, experimental) | H-K-05 | Workstation | hw-queued | — | 0.1.0 | Template `workloads/hw/campaign-profile-inference.json`; mechanism tested with simulated workload on a fake root |
| VL-1.4-01 | `.deb`: reproducible build, lintian, lifecycle install → use → upgrade (modified conffile) → remove → purge | — | Ubuntu 26.04 chroot (cloud host) + CI container | unit-tested | CI job `deb` | 0.1.0 | Lintian clean except `initial-upload-closes-no-bugs`; models kept on purge |
| VL-1.4-02 | Trust-boundary fixes F1–F7 (journal re-validation, txn IDs, O_NOFOLLOW, trusted trees, boot-ID expiry, sudo precondition, GPU restorability) | — | CI (CPU) | unit-tested | CI run on the PR | 0.1.0 | `architecture/trust-boundary-review.md`; NVIDIA path on a fake `nvidia-smi` only (see VL-1.3-04) |
| VL-1.4-03 | Reference image (vm flavor) boots UEFI → shim → GRUB → kernel; CLI, doctor, engine verify, CPU inference, profile apply/rollback inside the image | — | QEMU/TCG (no KVM), OVMF | unit-tested | `state/image-prototype-report.md` | 0.1.0 | Emulated CPU: timings are not representative of any hardware |
| VL-1.4-04 | A/B update with sha256 check, trial boot, health confirmation, data persistence, rollback, automatic fallback from an unbootable slot (trial-boot watchdog) | — | QEMU/TCG, i6300esb | unit-tested | `state/image-prototype-report.md` | 0.1.0 | Three real defects found and fixed by the VM runs (report §Findings) |
| VL-1.4-05 | `hw` flavor boots on the workstation with Secure Boot enabled; lockdown state as expected | — | Workstation | hw-queued | — | 0.1.0 | Not built/booted on hardware |
| VL-1.4-06 | NVIDIA driver via `ubuntu-drivers` in the `hw` flavor; `ailx doctor` sees the GPU; CUDA engine build | — | Workstation | hw-queued | — | 0.1.0 | Proprietary driver not shipped in the image (optional component) |
| VL-1.4-07 | Trial-boot watchdog on real chipsets (`sp5100_tco` on Zen 4, `iTCO_wdt` on the i5-11500), fallback after a hung trial boot | — | Workstation, Proxmox host | hw-queued | — | 0.1.0 | Only the emulated i6300esb was exercised |
| VL-1.4-08 | qcow2 image on Proxmox (KVM, virtio, OVMF) incl. A/B update | — | Proxmox server | hw-queued | — | 0.1.0 | Boot times under KVM unknown |
