# Consolidated Opportunity Matrix and Prioritization

> Status: Phase 0 synthesis · Last updated: 2026-11-26 · Inputs: O-* tables in 01–06 · **Scores are the lead architect's judgment, not measurements**

## Summary

- 79 opportunities from six research documents were merged into **30
  consolidated items** (duplicates across documents merged; source IDs kept).
- **P0 — foundation** (must exist before any claim): benchmark harness with
  rigorous statistics, quality gate, hardware probe/fingerprint, telemetry
  with wall-meter calibration.
- **P1 — first value** (high expected value, Tier T0/T1, quickly testable on
  the first hardware): GPU power-limit tuning for energy, MoE/CPU-offload
  planner with CCD-aware placement, runtime-parameter tuning, model-loading
  path, lossless serving features (prefix caching, speculative decoding),
  memory/PSI policy.
- **P2 — platform build-out**: sched_ext profiles, VRAM broker, engine gateway
  (reuse), self-optimizer search + change-point detection, image pipeline with
  rollback, supply chain.
- **P3 — research track**: GPU arbitration (XSched/gpu_ext), own AI-aware
  sched_ext scheduler, consumer per-op performance database, LLM-assisted knob
  proposals.
- **Deprioritized with reasons**: GDS on GeForce, kernel PGO/LTO, x86-64-v3
  rebuilds, static CPU isolation, IOMMU passthrough, MPS by default, two-machine
  model splitting.

## Scoring method

For each item: **B** benefit (1–5), **C** confidence that the benefit is real
given current evidence (1–5; A/B sources and mechanistic arguments score
higher than anecdotes), **F** feasibility (1–5), **R** risk (1–5, higher =
riskier), **E** effort (S=1, M=2, L=3, XL=5).

`Priority = (B × C × F) / (E × R)` — a transparent heuristic for ordering,
not a prediction. Foundation items (P0) are prioritized by dependency, not by
score: everything else needs them.

## P0 — Foundation (dependency-driven)

| # | Item | Merges | Why first | Level |
|---|------|--------|-----------|-------|
| 1 | **Benchmark harness**: manifests, RMIT, A/A calibration, multi-level repetition, effect-size CIs, winner re-validation, environment fingerprint, result store | O-S-01, O-S-14, O-H-12, O-L-01 (part) | Noise is the main failure mode of auto-tuning [S-10][S-47][S-50]; no claim without it (ADR-0003) | platform |
| 2 | **Quality gate service**: KLD + top-token agreement + flip rate (+ long-context for KV knobs) with reference-logit store; image/audio/embedding gates | O-R-02, O-S-07, O-L-04, O-R-13 | Every quantization is lossy and accuracy hides damage [R-24]; kernel bugs silently destroy quality [R-07] | platform |
| 3 | **Hardware probe + capability schema** (measured, not just datasheet) | O-L-03 | Inputs for planner and fingerprint; vendor-neutral schema is a gap [L-*] | userspace |
| 4 | **Telemetry & energy stack**: NVML (energy counter + instant), privileged RAPL collector, PSI, DRM fdinfo, scx stats, wall-meter calibration | O-K-09, O-H-13, O-S-12 | Software energy counters are not ground truth [S-29][S-30][S-33]; RAPL must stay root-only [K-50] | userspace |

## P1 — First value

Items are grouped by theme; the `Score` column gives the heuristic order
(highest first: #6, #5, #13, #11, #8, #7, #10, #9, #12).

| # | Item | Merges | B | C | F | R | E | Score | Quality | Key hypotheses |
|---|------|--------|---|---|---|---|---|-------|---------|----------------|
| 5 | **GPU power-limit tuning for J/token at fixed SLO** | O-S-02 | 4 | 3 | 5 | 1 | 1 | 60 | n.a. (lossless) | H-S-01 |
| 6 | **Runtime-parameter tuning wrapper** (llama-bench / `vllm bench sweep` / auto_tune with goodput objective) — also defines arm U1 | O-S-03, O-R-07 | 4 | 4 | 5 | 1 | 1 | 80 | lossless (verify) | H-L-01, H-L-02 |
| 7 | **MoE / CPU-offload planner** (`--n-cpu-moe`, `-ot`, KTransformers) with **CCD-aware placement** and X3D mode | O-H-03, O-R-05, O-L-07, O-H-01, O-K-11 | 5 | 4 | 4 | 2 | 2 | 20 | lossless at same precision | H-H-01, H-H-06, H-K-09 |
| 8 | **Prefix caching default for agents + host-RAM KV tier** | O-R-03, O-H-10, O-R-10, O-L-06 | 4 | 4 | 5 | 2 | 1 | 40 | lossless | H-R-04 (related) |
| 9 | **Speculative decoding presets** (n-gram → draft/EAGLE-3/MTP) | O-R-04 | 3 | 4 | 4 | 2 | 2 | 12 | lossless if verified | H-R-04 |
| 10 | **Model-loading path**: cold/warm measurement, parallel pinned/O_DIRECT loader where engines support it, page-cache budget | O-H-05, O-S-10, O-L-08 | 3 | 3 | 4 | 1 | 2 | 18 | lossless | H-S-06, H-H-03 |
| 11 | **Memory & pressure policy**: THP/mTHP, MGLRU `min_ttl_ms`, zswap, `memory.min/high` per class, PSI-triggered admission control | O-K-05, O-K-08 | 3 | 3 | 5 | 1 | 1 | 45 | n.a. | H-K-05 |
| 12 | **Cache-aware scheduling evaluation** (`SCHED_CACHE`, kernel ≥7.2) and upstream feedback | O-K-04 | 3 | 2 | 3 | 2 | 2 | 4.5 | n.a. | H-K-01 |
| 13 | **Reproducible server setup** (Proxmox `host` CPU, KSM/balloon off, pinning) and controller role | O-H-11, O-H-12 | 2 | 5 | 5 | 1 | 1 | 50 | n.a. | H-H-09 |

Note: #12 scores low on confidence but is kept in P1 because it is a cheap test
of a *new default* that could silently regress multi-CCD CPU inference on
future kernels (RK-16).

## P2 — Platform build-out

| # | Item | Merges | B | C | F | R | E | Score | Quality | Key hypotheses |
|---|------|--------|---|---|---|---|---|-------|---------|----------------|
| 14 | **sched_ext opt-in profiles** + GPU-aware CPU placement (DRM fdinfo/NVML) | O-K-02, O-K-01, O-S-08, O-K-03 | 3 | 3 | 4 | 1 | 2 | 18 | n.a. | H-K-02, H-K-03, H-S-09 |
| 15 | **Engine gateway & bundle abstraction**, reusing an existing launcher (RamaLama/Lemonade/LocalAI) rather than building one | O-R-01, O-L-02 | 4 | 4 | 4 | 2 | 3 | 10.7 | lossless | — |
| 16 | **VRAM broker** across engines (sleep/unload/offload hooks), later dmem cgroups | O-R-06, O-K-07 | 4 | 3 | 3 | 3 | 3 | 4 | lossless | H-R-05, H-K-10 |
| 17 | **Self-optimizer search** (constrained multi-objective BO over Tier-R knobs) + **change-point detection** (Otava) | O-S-04, O-S-06, O-L-01 | 4 | 3 | 4 | 2 | 2 | 12 | gated | H-S-03, H-S-08 |
| 18 | **Image pipeline with rollback**: bootc/mkosi prototypes, boot counting + health checks, CI-signed NVIDIA open modules, vendor variants | O-S-05, O-L-05, O-D-02, O-D-07 | 4 | 3 | 3 | 3 | 3 | 4 | n.a. | H-L-05, H-D-04 |
| 19 | **Supply chain & legal hygiene**: SBOM, SLSA provenance, reproducible-build checks, model fetcher with licence gate | O-D-05, O-D-06 | 3 | 5 | 4 | 1 | 2 | 30 | n.a. | H-D-09 |
| 20 | **Warm-start**: persisted JIT/autotune caches; cuda-checkpoint + CRIU snapshots | O-S-09, O-H-09 | 3 | 2 | 3 | 2 | 2 | 4.5 | lossless | H-H-05 |
| 21 | **Diffusion/video presets**: "reference" (lossless levers) vs labeled "fast" (lossy) | O-R-09 | 3 | 4 | 4 | 2 | 2 | 12 | configurable-lossy | — |
| 22 | **Distro-agnostic delivery** (sysext/confext + OCI) and Nix-pinned stacks as option | O-D-01, O-D-08 | 3 | 3 | 4 | 2 | 2 | 9 | n.a. | H-D-02, H-D-06 |
| 23 | **NPU offload of always-on small models** (ASR, embeddings, VAD) | O-R-12 | 3 | 2 | 2 | 3 | 2 | 2 | configurable-lossy | — (no NPU test hardware) |
| 24 | **Fine-tuning profile for 24 GB** (QLoRA, MoE SFT) | O-R-15 | 3 | 4 | 4 | 2 | 2 | 12 | configurable-lossy | — |

## P3 — Research track (long-term, high uncertainty)

| # | Item | Merges | Why research | Key hypotheses |
|---|------|--------|--------------|----------------|
| 25 | **Preemptive multi-workload GPU arbitration** (XSched-style), **eBPF GPU policies** (gpu_ext), drm_sched FAIR | O-L-09, O-L-10, O-K-15 | Strong in papers, not productized; proprietary-driver limits; stability risk [L-65][L-66][L-68][K-48][K-59] | H-L-04, H-K-10 |
| 26 | **Own AI-aware sched_ext scheduler** (GPU-feeder/expert-compute/interactive classes, CCD-asymmetry aware) | O-D-04, builds on #14 | Only if #14 shows the existing schedulers leave measurable gains | H-K-02 |
| 27 | **Per-operation performance DB for consumer GPUs** to prune search (AIConfigurator-style) | O-S-11 | No consumer data exists [S-16] | — |
| 28 | **LLM-assisted knob proposals**, allow-listed and harness-verified | O-S-13 | Useful only once registry and harness are mature | — |
| 29 | **Model-store efficiency**: content-addressed store, lossless compression (ZipNN) for BF16 | O-H-07, O-H-08 | Storage benefit clear, speed benefit only for BF16 | H-H-04 |
| 30 | **Determinism / batch-invariant mode** for reproducible evals | O-R-14 | Cost estimates contradict (≈20% to 2×) [S-65] | H-S-05, H-R-08 |

## Deprioritized (with reasons)

| Item | Source | Reason |
|------|--------|--------|
| GDS on RTX 4090 | O-H-06 | GeForce is compatibility-mode only; one NVMe already bounds the path; P2PDMA path has a KASLR issue [H-45][H-46] (H-H-03) |
| Kernel PGO/AutoFDO/Propeller/ThinLTO | O-K-10 | Published gains are low single digits on server/network benchmarks; no AI evidence [K-52][K-53] (H-K-07) |
| x86-64-v3/v4 full rebuilds | O-D-03 | Mixed independent results; kernel barely benefits [D-18][K-54] (H-D-01); revisit only for hot libraries |
| Static CPU isolation (`isolcpus`/`nohz_full`) | O-K-12 | Targets DPDK-class workloads; wastes desktop cores (H-K-08) |
| IOMMU passthrough | — | Expected effect below noise (H-K-04); security trade-off → opt-in only |
| CUDA MPS by default | O-R-16 | GeForce support unclear; weakens isolation [R-41][R-42] (H-R-09) |
| Splitting one model across both machines | — | Adds capacity, not speed; RPC backend insecure [H-41] (H-H-07) |
| resctrl partitioning on client CPUs | O-K-13 | Limited client hardware support; low expected effect |
| Core scheduling for agent sandboxes | O-K-14 | Relevant for security architecture of agents, not a Phase 1–2 performance item |

## Where the real innovation is

Most single techniques above are **known elsewhere**. The novel contribution
of AI Linux is the combination (confirmed as a gap in [L-*] §F6):

1. A **closed, quality-gated, rollback-safe loop** across OS, driver, GPU and
   runtime knobs (#1, #2, #17, #18).
2. A **knob registry with security/quality/reversibility metadata** and
   evidence links, usable by other projects (#3, #17).
3. **CPU-side optimization for hybrid inference on asymmetric desktop CPUs**
   (#7, #12, #14, #26) — the one area where OS-level work plausibly matters
   for local AI (H-D-03, H-S-02).
4. **Cross-engine VRAM arbitration on consumer GPUs** (#16, later #25).
5. **Reproducible, comparable results** keyed by hardware profile and
   fingerprint, shared opt-in (#1, #3).
