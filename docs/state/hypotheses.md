# Hypothesis Register

> Status: all **untested** (Phase 0, updated by Phase 0.1 review) · Last updated: 2026-11-26 · Full statements, rationale and risks: see the linked research documents · Test references: `docs/architecture/hardware-test-plan.md` (HW-*, E1–E15) and `docs/research/05-base-distributions-and-licensing.md` §1.9 (E-01…E-09)

Priority: **P1** = tested in the first hardware block; **P2** = Phase 2–3;
**P3** = research track or opportunistic. Status values: `untested`,
`in-test`, `confirmed`, `refuted`, `inconclusive` (with result IDs).

## Strategic hypotheses (decide where the project invests)

| ID | Hypothesis (short) | Test | Prio | Status |
|----|--------------------|------|------|--------|
| [H-L-01](../research/01-landscape.md) | Runtime/quantization/offload configuration yields larger gains than OS/kernel tuning | HW-2 (arms U0/U1/U2/A) | P1 | untested |
| [H-S-02](../research/06-self-optimization-and-benchmarking.md) | OS-level gains for GPU-resident decode are <2% over Ubuntu+TuneD; gains concentrate in loading, CPU offload, tail latency | HW-2, E2, E4, E7 | P1 | untested |
| [H-D-03](../research/05-base-distributions-and-licensing.md) | Kernel/scheduler choice matters little for GPU-bound inference but matters for CPU-offload/hybrid | E2, E3, E7, E-02 | P1 | untested |
| [H-L-02](../research/01-landscape.md) | A closed tune→benchmark→quality-gate→rollback loop beats runtime defaults on ≥ half of machines without quality loss | HW-2 (U1 vs U0), Phase 2 optimizer | P1 | untested |
| [H-D-02](../research/05-base-distributions-and-licensing.md) | Distro-agnostic layer reaches most users at < half the maintenance of an own image | E-06, E-08 | P2 | untested |

## Measurement and quality method

| ID | Hypothesis (short) | Test | Prio | Status |
|----|--------------------|------|------|--------|
| [H-S-03](../research/06-self-optimization-and-benchmarking.md) | Without RMIT + re-validation, ≥30% of adopted "winners" vanish on re-test; with them <5% | HW-1 A/A, Phase 2 campaigns | P1 | untested |
| [H-S-04](../research/06-self-optimization-and-benchmarking.md) | KLD + same-top-p detects numerics changes more sensitively and cheaply than task accuracy | E9 | P1 | untested |
| [H-S-05](../research/06-self-optimization-and-benchmarking.md) | Batched serving at T=0 yields >1 unique completion → exact-match gates give false positives | E9 | P1 | untested |
| [H-R-08](../research/04-runtimes-and-workloads.md) | Bitwise nondeterminism is not quality loss (KLD unchanged) | E9 | P1 | untested |
| [H-R-03](../research/04-runtimes-and-workloads.md) | KLD/flips suffice for weight-quant gating; KV-cache quant needs long-context tests | E9 | P1 | untested |
| [H-S-07](../research/06-self-optimization-and-benchmarking.md) / [H-H-08](../research/03-hardware-io-and-model-loading.md) | NVML on RTX 4090 samples partially/averages; short-request energy off by >5–10% | HW-1 energy calibration | P1 | untested |
| [H-S-08](../research/06-self-optimization-and-benchmarking.md) | E-Divisive detects 5% step regressions within 5 points at ≤1 false alarm/metric/quarter | Phase 2 (replayed series) | P2 | untested |
| [H-S-10](../research/08-architecture-review.md) | Fixed output length reduces run-to-run CoV of throughput metrics substantially vs natural lengths | HW-1 A/A | P1 | untested |
| [H-H-09](../research/03-hardware-io-and-model-loading.md) | Proxmox default CPU type hides AVX2/AVX-512 → much slower CPU inference than `host` | HW-1 | P1 | untested |

## CPU, memory and scheduling

| ID | Hypothesis (short) | Test | Prio | Status |
|----|--------------------|------|------|--------|
| [H-H-01](../research/03-hardware-io-and-model-loading.md) | CPU-side memory-bound decode on one 7950X3D CCD is capped near the per-CCD fabric limit; spanning both CCDs approaches DRAM limit; V-Cache does not help weight streaming | HW-1 bandwidth, E2 | P1 | untested |
| [H-K-09](../research/02-kernel-and-os.md) | X3D "cache" mode helps small working sets (tokenizer, small embedders), not >1 GB model decode | E2 | P1 | untested |
| [H-K-01](../research/02-kernel-and-os.md) | `SCHED_CACHE` (≥7.2) can reduce throughput of memory-bound processes with 2–6 avg. running threads on a 7950X3D (aggregation window, source-verified [V-01]) | E3 | P1 | untested |
| [H-K-11](../research/08-architecture-review.md) | Explicit CPU affinity/cpusets bound `SCHED_CACHE` aggregation, giving a lockdown-compatible control | E3 | P1 | untested |
| [H-H-06](../research/03-hardware-io-and-model-loading.md) | A ~120B MoE with experts in RAM is borderline on 64 GB (faults/RSS pressure in long sessions) | E2 | P1 | untested |
| [H-H-02](../research/03-hardware-io-and-model-loading.md) | Batch-1: CPU compute of offloaded layers beats PCIe weight streaming; streaming wins at larger batch/prefill | E5 | P1 | untested |
| [H-K-02](../research/02-kernel-and-os.md) | sched_ext choice matters only under CPU contention | E7 | P2 | untested |
| [H-K-03](../research/02-kernel-and-os.md) | EEVDF slice hints recover most of any scx benefit | E7 | P2 | untested |
| [H-R-10](../research/08-architecture-review.md) | Userspace PGO/BOLT builds of CPU inference paths (e.g. llama.cpp CPU backend) give measurable CPU-decode/prefill gains, unlike kernel PGO | E12 (variant) | P2 | untested |
| [H-S-09](../research/06-self-optimization-and-benchmarking.md) | Latency-oriented sched_ext improves interactive p99 under load, not GPU-bound throughput (>1%) | E7 | P2 | untested |
| [H-K-05](../research/02-kernel-and-os.md) | DAMON-driven THP collapse raises CPU-inference tokens/s without app changes | E6 | P2 | untested |
| [H-K-08](../research/02-kernel-and-os.md) | `isolcpus`/`nohz_full` do not help single-user GPU inference | E7 (variant) | P3 | untested |
| [H-H-10](../research/03-hardware-io-and-model-loading.md) | Zen 4 AVX-512 speeds up prefill, barely decode, vs AVX2 | E12 | P2 | untested |
| [H-D-01](../research/05-base-distributions-and-licensing.md) | x86-64-v3/v4 repo rebuilds yield little for AI workloads | E12, E-01 | P2 | untested |
| [H-K-07](../research/02-kernel-and-os.md) | Kernel PGO/LTO gains are within noise for AI work | Phase 3 (kernel builds) | P3 | untested |

## GPU, energy, loading and I/O

| ID | Hypothesis (short) | Test | Prio | Status |
|----|--------------------|------|------|--------|
| [H-S-01](../research/06-self-optimization-and-benchmarking.md) | RTX 4090 decode keeps ≥95% tokens/s at ~70% power limit and improves J/token by ≥15% | E1 | P1 | untested |
| [H-S-06](../research/06-self-optimization-and-benchmarking.md) | Cold load is dominated by the read path, not device bandwidth; read-path tuning changes it by >20% | E4 | P1 | untested |
| [H-H-03](../research/03-hardware-io-and-model-loading.md) | GDS gives no load-throughput gain over a pinned parallel loader on this workstation | E4 | P2 | untested |
| [H-H-04](../research/03-hardware-io-and-model-loading.md) | ZipNN cuts BF16 cold load ≈ proportionally to size reduction; ≈0 for ≤4-bit | Phase 2 | P3 | untested |
| [H-H-05](../research/03-hardware-io-and-model-loading.md) | cuda-checkpoint + CRIU restore beats cold start for ≤20 GB models | E13 | P2 | untested |
| [H-K-04](../research/02-kernel-and-os.md) | IOMMU mode effect on AI workloads is below noise (single GPU) | E14 | P3 | untested |
| [H-K-06](../research/02-kernel-and-os.md) | HMM/UVM oversubscription is slower than explicit runtime offload | E5 (variant) | P3 | untested |

## Runtimes and workloads

| ID | Hypothesis (short) | Test | Prio | Status |
|----|--------------------|------|------|--------|
| [H-R-01](../research/04-runtimes-and-workloads.md) | llama.cpp CUDA decode on 4090 is within 15% of the fastest NVIDIA-specific engine at matched bpw | E8 | P1 | untested |
| [H-R-02](../research/04-runtimes-and-workloads.md) | Vulkan reaches ≥85% of CUDA decode but materially less prefill | E8 | P2 | untested |
| [H-R-04](../research/04-runtimes-and-workloads.md) | Lossless speculation pays for agents/coding, not high-temperature chat | E10 | P2 | untested |
| [H-R-05](../research/04-runtimes-and-workloads.md) | A VRAM broker enables LLM + diffusion co-residence on 24 GB without OOMs, switch cost of seconds | E11 | P2 | untested |
| [H-R-06](../research/04-runtimes-and-workloads.md) | FP4 models on Ada gain nothing over INT4 W4A16 | E8 (variant) | P3 | untested |
| [H-R-07](../research/04-runtimes-and-workloads.md) | Consumer AMD: ROCm 10 wins batched serving, Vulkan single-user | Phase 4 (AMD hardware) | P3 | untested |
| [H-R-09](../research/04-runtimes-and-workloads.md) | MPS works on RTX 4090 and improves concurrent small-batch throughput | E11 (opt-in) | P3 | untested |
| [H-H-07](../research/03-hardware-io-and-model-loading.md) | Splitting a model across both machines lowers tokens/s; it only adds capacity | E15 | P3 | untested |

## Platform, delivery, ecosystem

| ID | Hypothesis (short) | Test | Prio | Status |
|----|--------------------|------|------|--------|
| [H-L-03](../research/01-landscape.md) | A shared capability schema would be adopted by ≥2 launchers | Outreach, Phase 2 | P3 | untested |
| [H-L-04](../research/01-landscape.md) | XSched-style arbitration cuts interactive p99 on one consumer GPU without raising crash rates | Research track | P3 | untested |
| [H-L-05](../research/01-landscape.md) | Image-based OS with optional driver layers rolls back failed driver/runtime upgrades reliably, incl. NVIDIA + Secure Boot | HW-5, E-04, E-05 | P2 | untested |
| [H-L-06](../research/01-landscape.md) / [H-D-05](../research/05-base-distributions-and-licensing.md) | Distro-packaged accelerator stacks lag upstream by months | E-03 | P2 | untested |
| [H-K-10](../research/02-kernel-and-os.md) | Open-stack multi-agent isolation (scx sub-schedulers + dmem + drm_sched FAIR) is feasible on amdgpu/xe | Phase 4 | P3 | untested |
| [H-D-04](../research/05-base-distributions-and-licensing.md) | Fedora bootc gives the best rollback story with least custom code; NVIDIA needs CI-signed kmods | E-04, E-05 | P2 | untested |
| [H-D-06](../research/05-base-distributions-and-licensing.md) | NixOS maximizes reproducibility but raises the contributor barrier | Onboarding trial | P3 | untested |
| [H-D-07](../research/05-base-distributions-and-licensing.md) | Yocto/Buildroot unsuitable for dGPU workstations, suit edge appliances | Desk review | P3 | untested |
| [H-D-08](../research/05-base-distributions-and-licensing.md) | A Debian-based image is the most auditable base | E-07 | P2 | untested |
| [H-D-10](../research/08-architecture-review.md) | A composefs/fs-verity verified read-only model store adds no measurable load-time cost vs a plain read-only store | E4 (variant), Phase 3 | P3 | untested |
| [H-D-09](../research/05-base-distributions-and-licensing.md) | `Apache-2.0 OR MIT` + GPL-2.0 kernel/BPF minimizes licence friction | Legal review | P1 (non-HW) | untested |

Total: 57 hypotheses (L 6, K 11, H 10, R 10, D 10, S 10) — four added by the Phase 0.1 review.
