# Hardware Test Plan: Ryzen 9 7950X3D + RTX 4090 Workstation and i5-11500 Proxmox Server

> Status: Phase 0 plan (nothing executed) · Last updated: 2026-11-26 · Methodology: `benchmarking.md` · Evidence: `docs/research/03-hardware-io-and-model-loading.md` [H-*], `02-kernel-and-os.md` [K-*], `04-runtimes-and-workloads.md` [R-*], `06-self-optimization-and-benchmarking.md` [S-*]

## Summary

- The workstation is the **primary measurement target**; the Proxmox server is
  the **controller, result store, energy logger and CPU-only target** — never
  used for GPU numbers (`06-self-optimization-and-benchmarking.md` design
  implication 15; O-H-11).
- Order of work: **inventory → calibration (micro-benchmarks, A/A noise) →
  Ubuntu baseline (U0/U1/U2) → single-factor experiments ranked by expected
  value → distro bake-off → soak/rollback drills.**
- Every experiment maps to hypotheses in `docs/state/hypotheses.md`; outcomes
  (including null results) go to the validation ledger.
- All performance numbers in this document are **theoretical bounds from
  datasheets** (labeled) — no measurements exist yet.

## 1. Hardware facts relevant to test design

| Item | Fact | Consequence for tests | Source |
|------|------|-----------------------|--------|
| 7950X3D topology | 2 CCDs; one with 96 MB L3 (V-Cache), one with 32 MB; 16C/32T | Identify V-Cache CCD CPUs via `/sys/devices/system/cpu/cpu*/cache/index3/size`; test placement per CCD | [H-03][H-01] |
| CCD fabric | ≈64 GB/s read per CCD at FCLK 2 GHz (*theoretical*) < dual-channel DDR5 peak | Memory-bound CPU work may need both CCDs (H-H-01) | [H-08] |
| X3D mode | `amd_x3d_vcache` sysfs switch "frequency"/"cache" (since 6.13) | Runtime knob (T1) | [K-23][H-05] |
| AVX-512 | Zen 4 double-pumped; VNNI and BF16 present; no AMX | KTransformers AMX path n/a; AVX-512 paths apply (H-H-10) | [H-06][H-07] |
| DDR5 | Official DDR5-5200 at 2 DIMMs, DDR5-3600 at 4 DIMMs; DDR5-6000 is an EXPO overclock | Verify 2×32 GB 1DPC; record EXPO state; stability test before campaigns | [H-01][H-09][H-10] |
| RTX 4090 | 24 GB GDDR6X, 1008 GB/s (*theoretical*), PCIe 4.0 x16, ReBAR, 450 W TGP, no NVLink, P2P unsupported | Verify link `LnkSta` Gen4 x16 and ReBAR; power-limit sweep within vendor range only | [H-12][H-14][H-15][H-18] |
| GPU formats | FP8 supported on Ada; no native FP4; FlashAttention-3/4 not applicable (FA2 family) | Exclude FP4/FA3 "speedups"; H-R-06 | [R-09][R-18][R-20] |
| GPUDirect Storage | GeForce = compatibility mode only; P2PDMA mode needs open module 570+ and has a known KASLR issue | GDS test is low priority; **never disable KASLR** | [H-45][H-46] |
| NVMe | PCIe 5.0 x4, class leaders ≈14.8 GB/s rated (*datasheet*) | Cold-load floor ≈ size / 14.8 GB/s (*theoretical*) | [H-20][H-21] |
| i5-11500 | Rocket Lake 6C/12T, AVX-512 + VNNI (verify flags), DDR4-3200 dual-channel (51.2 GB/s *theoretical*), UHD 750 iGPU, no SR-IOV | CPU-only target; iGPU secondary (OpenVINO/Level Zero support contested) | [H-22][H-24][H-27][H-28] |
| Proxmox VE | 9.2, Debian 13.5 base, kernel 7.0; default VM CPU type `x86-64-v2-AES` hides AVX2/AVX-512 | Benchmark VMs must use CPU type `host`; KSM and ballooning off | [H-30][H-32][H-86] |

Theoretical decode bounds (batch 1, weights only, KV ignored; formula
tokens/s ≤ BW / bytes-per-token — **not benchmarks**) from
`03-hardware-io-and-model-loading.md` §3: e.g. ~8B dense at ~4.9 bpw ≤ ~206 t/s
from VRAM and ≤ ~17 t/s from DDR5-5200. These bounds are used only to sanity-
check measurements (a measurement above the bound indicates a measurement error).

## 2. Setup

### 2.1 Workstation

- **Disks**: model store on the PCIe 5.0 NVMe, dedicated partition formatted
  identically for all arms (ext4 default; FS is a variable only in E6). OS
  installs on separate partitions or a second drive; record which.
- **OS installs**: (1) Ubuntu 26.04 LTS stock = baseline (Secure Boot on);
  (2) Ubuntu 26.04 + AI Linux layer (arm A) — may share install (1) with the layer
  disabled/enabled, provided the layer's off state is verified to be identical
  to stock (fingerprint diff); (3) later: bake-off hosts (Fedora 45, Debian 13,
  CachyOS, NixOS) and CI image prototypes.
- **Research boot entries** that need a newer kernel (e.g. ≥7.2 for
  `SCHED_CACHE` [K-19]) are separate, labeled boot entries; they keep Secure
  Boot/lockdown on unless an experiment explicitly requires otherwise, in which
  case the entry is offline-only and labeled `explicit-opt-in` (ADR-0004).
- **Instrumentation**: logging wall meter (model TBD), ambient temperature
  sensor, NVML high-rate sampler, privileged RAPL collector (`energy_uj` stays
  root-only [K-50][H-76]).

### 2.2 Proxmox server

| Role | Form | Notes |
|------|------|-------|
| Controller / orchestrator / result store | LXC or VM | Drives campaigns over the network; stores result records; runs change-point detection |
| Wall-meter + sensor logger | LXC | Timestamps aligned with workstation (NTP/PTP; record offset) |
| CI runner | VM | AVX-512 + Gen12 iGPU coverage for builds/tests (O-H-11) [H-22][H-27] |
| CPU benchmark target | VM, CPU type `host`, KSM off, ballooning off, pinned vCPUs, hugepages reserved at boot | Ubuntu 26.04 guest = CPU baseline; other guests idle during runs [H-32] |
| Model cache / mirror | LXC | Serves model files to workstation only outside measurement windows |

Network: dedicated wired link; control traffic only during runs.

## 3. Phases and experiments

### HW-0 Inventory (≈1 day)

BIOS/AGESA and firmware versions, memory configuration and profile, CPU→CCD
map, GPU VBIOS/driver/link/ReBAR, NVMe model/firmware/slot, Proxmox version and
host kernel, i5 CPU flags (`lscpu`), iGPU visibility. Output: hardware profile
v0 + fingerprint v0 (manually, if the probe does not exist yet).

### HW-1 Calibration (≈2–4 days)

| Step | Measures | Hypotheses |
|------|----------|-----------|
| Memory bandwidth per CCD and both CCDs (STREAM-like) at the configured memory profile | Real vs theoretical bandwidth | H-H-01 |
| NVMe fio: sequential read O_DIRECT and buffered, QD sweep | Storage ceiling | H-S-06 |
| Host↔device bandwidth (e.g. nvbandwidth) | PCIe ceiling | H-H-02 |
| Idle power (wall, NVML, RAPL); NVML vs wall under square-wave GPU load | Energy calibration | H-S-07, H-H-08 |
| A/A campaigns for each smoke-suite workload | Noise floor per metric | H-S-03 |
| Known-effect injection (e.g. lower GPU power limit, restrict cores) | Harness sensitivity | — |
| Proxmox VM CPU type `host` vs default | ISA exposure, CPU inference effect | H-H-09 |

### HW-2 Ubuntu baseline (≈1 week)

Arms U0, U1, U2 (`benchmarking.md` §1) on the smoke and standard suites;
CPU-only subset (W-EMB-1, W-ASR-1, small LLM) on the server VM. Deliverable:
**Baseline Report v1** with result IDs, noise floors and theoretical-bound
sanity checks. This also tests H-L-01 / H-S-02 directly (U1 vs U0 vs U2).

### HW-3 Single-factor experiments (ranked by expected value; Tier T0/T1 unless noted)

| ID | Experiment | Arms / factor levels | Key workloads | Hypotheses | Notes |
|----|-----------|----------------------|---------------|-----------|-------|
| E1 | GPU power-limit sweep | 50–100% in 5% steps, RMIT | W-LLM-1/2 decode, prefill, W-IMG-1 | H-S-01 | Only reductions within vendor range; energy via wall meter |
| E2 | MoE expert offload fit and CCD placement | `--n-cpu-moe` levels × CPU set (V-Cache CCD / other CCD / both) × X3D mode | W-LLM-3 | H-H-01, H-H-06, H-K-09 | Highest-value CPU-side experiment |
| E3 | Cache-aware scheduling | Kernel ≥7.2 with `SCHED_CACHE` enabled vs disabled (build variants or debugfs on an offline research entry) | W-LLM-3, CPU-only decode at 4–16 threads | H-K-01 | Baseline Ubuntu 26.04 (7.0) is unaffected; relevant for newer kernels/HWE |
| E4 | Model loading paths | mmap vs O_DIRECT/parallel pinned loader (e.g. fastsafetensors, Model Streamer) vs GDS compat; cold vs warm | W-LOAD-1 | H-S-06, H-H-03 | Stage-level timestamps |
| E5 | Offload strategy | CPU compute of offloaded layers (llama.cpp) vs weight streaming (vLLM CPU offload), batch 1–64 | W-LLM-2/3 | H-H-02 | |
| E6 | Memory policy | THP never/madvise/always, mTHP sizes, DAMON collapse; FS for model store (ext4 vs XFS) | W-LLM-3, W-LOAD-1 | H-K-05 | |
| E7 | sched_ext under contention | EEVDF vs scx_lavd / scx_bpfland / scx_layered / scx_cosmos; with and without background compile/agent load | W-MIX-2, W-AGT-1 | H-K-02, H-K-03, H-S-09 | Requires `CONFIG_SCHED_CLASS_EXT` (check `/sys/kernel/sched_ext`); kernel falls back on failure [K-08] |
| E8 | Engine/backend comparison | llama.cpp CUDA vs Vulkan vs ExLlamaV3 (matched bpw, KLD-checked) | W-LLM-1/2 | H-R-01, H-R-02 | Informs bundle defaults |
| E9 | Quality-gate design | Repeated greedy runs at concurrency 1/8/64; KLD vs task accuracy; KV-cache quantization long-context | W-LLM-1, W-SRV-1 | H-S-04, H-S-05, H-R-03, H-R-08 | Decides gate thresholds |
| E10 | Lossless serving features | Prefix caching on/off; speculative decoding presets | W-AGT-1, W-SRV-1 | H-R-04 | |
| E11 | GPU sharing | LLM + image on one GPU: naive vs engine sleep/unload hooks; MPS (opt-in) | W-MIX-1 | H-R-05, H-R-09 | MPS weakens isolation — opt-in only |
| E12 | ISA builds | AVX-512 vs AVX2 builds; later distro v3/v4 packages | CPU decode/prefill | H-H-10, H-D-01 | |
| E13 | Warm start | cuda-checkpoint + CRIU restore vs cold start | W-LOAD-1 (server engines) | H-H-05 | Driver ≥570; no UVM |
| E14 | IOMMU modes | strict vs lazy (default); passthrough only as labeled opt-in research | W-LOAD-1, W-LLM-1 | H-K-04 | Never `iommu=off` |
| E15 | Two-machine split | llama.cpp RPC across workstation + server | W-LLM-3 | H-H-07 | Low priority; isolated network only [H-41] |

### HW-4 Distro bake-off (Phase 2–3)

Experiments E-01…E-09 from `05-base-distributions-and-licensing.md` §1.9
with identical runtime bundles: ISA repos, kernel/scheduler, version lag,
module signing, rollback drills, maintenance effort, reproducible rebuilds,
sysext portability, SBOM/licence review.

### HW-5 Stability and rollback drills (Phase 2–3)

W-SOAK-1 (24 h) for every candidate profile that passes HW-3; scripted
upgrade/rollback cycles for image prototypes (boot counting, health checks)
(H-L-05, H-D-04).

## 4. Safety rules for all tests

1. Secure Boot, lockdown, CPU mitigations, IOMMU and LSMs stay at
   distribution defaults on baseline and product arms (ADR-0004).
2. Research-only configurations that weaken security run on separate,
   offline boot entries, are labeled `explicit-opt-in`, and their results are
   never used as product defaults.
3. Power/thermal: only power-limit reductions; no over-clocking or
   over-volting; abort on throttling or hardware error events.
4. Memory overclock (EXPO) is recorded, not changed during a campaign.
5. No model weights or user data leave the machines; results are stored
   locally on the controller.

## 5. Exit criteria for the first hardware block

- Baseline Report v1 published with result IDs and noise floors.
- A/A false-positive rate within target; harness sensitivity demonstrated.
- E1, E2 and E4 completed (highest expected value, all Tier T0/T1).
- Hypothesis register updated (confirmed / refuted / inconclusive).
