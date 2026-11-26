# Validation Ledger (deferred validation)

> Status: active · Last updated: 2026-11-26 · Policy: ADR-0010 (accepted)

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
