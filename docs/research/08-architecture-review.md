# Phase 0.1 — Independent Architecture Review

> Status: Phase 0.1 review · Last updated: 2026-11-26 · Scope: targeted verification and critique of Phase 0, not a re-run of the research · Source prefix: **V** · Evidence grading: see 00-method.md

## Summary

- **Most decision-relevant kernel claims hold up against the kernel source**
  (Linux master = 7.4-rc4, read via the GitHub mirror). Two errors were found
  and corrected: sched_ext cgroup sub-schedulers exist since **v7.1** (not 7.3),
  and **dmem reclaim is merged in v7.3** (Phase 0 said "under review").
- **`SCHED_CACHE` is real, default-on and debugfs-only** — and its effective
  window on a 7950X3D is narrower than Phase 0 implied: a process is
  aggregated into one CCD only while it averages **≤ 6 running threads**
  (SMT2, 16 logical CPUs per CCD). Default many-thread llama.cpp runs fall
  outside; few-thread CPU workers (MoE expert compute, embedding/ASR servers,
  serving workers) fall inside. H-K-01 was sharpened accordingly.
- **GPUDirect Storage claims confirmed**: full GDS mode only for Data Center
  and Quadro-class GPUs; NVMe P2PDMA has a known failure with KASLR, for which
  NVIDIA's workaround is `nokaslr` — forbidden by ADR-0004. GDS stays
  deprioritized.
- **Biggest architectural blind spot: unified-memory devices** (AMD Strix Halo
  APUs, NVIDIA GB10-class systems). The provider model assumed "VRAM vs RAM";
  on these devices the GPU-usable share of system RAM is a kernel/TTM limit.
  The capability model was extended with memory domains.
- **Other gaps fixed in the architecture**: model files treated as untrusted
  input (sandboxed runtimes, no pickle by default, digest verification);
  agent sandbox and service plane added as components; realtime (audio)
  workload class; coexistence with other tuning daemons; reuse of OCI/ModelPack
  for model packaging; composefs/fs-verity for verified images and models.
- **Methodology gaps fixed**: multiple-comparison control for screening
  campaigns, pre-registered analysis plans, fixed output lengths for
  throughput workloads, thermal/boost control, explicit power analysis.
- **ADRs 0005–0010 are sound**; recommendations: accept 0005, 0006, 0007,
  0010 with the amendments recorded in each ADR; accept 0008 after the
  probation/conflict amendments; keep 0009 proposed until legal review.
- **Phase 1.1 is now a directly executable specification**:
  `docs/architecture/phase1.1-measurement-foundation.md`.

## 1. Verification of decision-relevant claims

Legend: ✅ confirmed · ✏️ corrected · ⚠️ confirmed with caveat · ❓ still open.

| # | Claim (Phase 0) | Checked against | Verdict |
|---|-----------------|-----------------|---------|
| 1 | `CONFIG_SCHED_CACHE` exists, `default y`, depends on SMP | `init/Kconfig` (master) [V-02] | ✅ |
| 2 | Merged for 7.2 | `init/Kconfig` at tags v7.1 (absent), v7.2, v7.3 (present) [V-02] | ✅ |
| 3 | Active only on multi-LLC topologies; user switch default on | `kernel/sched/topology.c`: `sched_cache_present` set iff `has_multi_llcs`; `sysctl_sched_cache_user = 1` [V-02] | ✅ |
| 4 | Toggle is debugfs-only (`/sys/kernel/debug/sched/llc_balancing/enabled` + 5 tunables) | `kernel/sched/debug.c` [V-01] | ✅ — no sysctl, boot parameter or prctl found |
| 5 | Footprint check only with NUMA balancing; thread rule ≈80% of LLC CPUs × SMT | `kernel/sched/fair.c`: `exceed_llc_capacity()` guarded by `sched_numa_balancing`; `fits_llc_nr()` = `nr_threads*5 < nr_cpus*4`, threads multiplied by `cpu_smt_num_threads`; single-threaded tasks skipped [V-01] | ✏️ sharpened: on 7950X3D window = ≤ 6 avg. running threads |
| 6 | sched_ext falls back to fair class on error, stall or SysRq-S | `Documentation/scheduler/sched-ext.rst`; watchdog timeout from `ops->timeout_ms`, capped by `SCX_WATCHDOG_MAX_TIMEOUT` [V-04] | ✅ (the "30 s" maximum not re-verified; constant defined outside the files read) |
| 7 | cgroup sub-schedulers "since 7.3" | `CONFIG_EXT_SUB_SCHED`: absent v7.0, present v7.1 [V-03] | ✏️ since 7.1 (feature-complete reported for 7.3 [K-17], grade C) |
| 8 | sched_ext BPF schedulers must be GPL-compatible | `kernel/bpf/verifier.c`: "struct ops programs must have a GPL compatible license" [V-06] | ✅ |
| 9 | x86: only PREEMPT, PREEMPT_LAZY (default), PREEMPT_RT | `kernel/Kconfig.preempt`: NONE needs `ARCH_NO_PREEMPT`, VOLUNTARY needs `!ARCH_HAS_PREEMPT_LAZY`; default LAZY where available [V-07] | ✅ |
| 10 | `amd_x3d_vcache` switches `frequency`/`cache` | `drivers/platform/x86/amd/x3d_vcache.c`; module parameter `x3d_mode`, default `frequency` [V-08] | ✅ (+ default mode added) |
| 11 | dmem reclaim "under review" | `kernel/cgroup/dmem.c`: no `ops->reclaim` at v7.2, present at v7.3 [V-05] | ✏️ merged in 7.3 |
| 12 | dmem regions registered by xe (doc example) | `ttm_resource.c`, `xe_ttm_vram_mgr.c`, `amdgpu_vram_mgr.c`, `nouveau_ttm.c` reference dmem cgroup APIs; `intel_region_ttm.c` (i915) does not [V-05] | ✅ + amdgpu, nouveau; NVIDIA out-of-tree ❓ |
| 13 | Per-CCD read link 32 B/cycle → ≈64 GB/s at 2 GHz FCLK | Chips and Cheese Zen 4 system-level analysis [V-10] | ✅ (theoretical; measured single-CCD DDR5-6000 value not found) |
| 14 | GDS: GeForce compat-mode only | NVIDIA GDS release notes: GDS mode for Data Center and Quadro (desktop) GPUs with CC > 6; all others compatibility mode [V-09] | ⚠️ GeForce is not named; "compat-only" is an inference from "all other cards" |
| 15 | P2PDMA mode fails with KASLR | GDS known issues: NVMe P2PDMA can fail on x86_64 with KASLR; workaround `nokaslr` [V-09] | ✅ — workaround is forbidden (ADR-0004) |
| 16 | ROCm 10.0 exists (TheRock-based) | ROCm 10.0 release notes, 2026-08-26 [V-18] | ✅ |
| 17 | scx schedulers are GPL | `sched-ext/scx` LICENSE (GPL-2.0); `scx_layered` crate `GPL-2.0-only` [V-17] | ✅ — note: **userspace parts derived from scx inherit GPL-2.0-only** |

**CCD placement (consolidated view).** Facts: two LLC domains (one 96 MB,
one 32 MB) [H-03]; per-CCD read link ≈64 GB/s theoretical vs 83.2 GB/s
DDR5-5200 dual-channel theoretical [V-10][H-01]; `amd_x3d_vcache` changes only
the *preference order* of CCDs, default `frequency` [V-08]; `SCHED_CACHE`
(≥7.2) packs few-thread processes into one LLC [V-01]. Hypotheses: CPU-side
memory-bound inference should span both CCDs (H-H-01); explicit affinity or
cpusets bound `SCHED_CACHE` aggregation because it is a load-balancing
preference within allowed CPUs (H-K-11, new). Decision: CCD placement is a
first-class, runtime-reversible knob (T1) of the planner, set via cpusets —
not via debugfs.

## 2. Overlooked technologies with high potential

| # | Technology | Why it matters for AI Linux | Evidence | Action |
|---|-----------|-----------------------------|----------|--------|
| T1 | **Unified-memory APUs/SoCs** (AMD Strix Halo; NVIDIA GB10-class) | Large models locally without a dGPU; GPU-usable memory is bounded by TTM limits (`ttm.pages_limit`, default ≈50% of RAM; `amdgpu.gttsize` deprecated; AMD states patches in ≥ 6.18.4) | [V-11] A (snippet), [V-12] C; DGX Spark [L-17] | Capability model gains **memory domains**; TTM limit becomes a T2 knob (`allowed`); test hardware wish-list |
| T2 | **OCI artifacts / CNCF ModelPack** for models | Standard, digest-addressed model packaging already used by Docker Model Runner and KitOps; registries for free; signing via OCI tooling | [V-13] B, [V-14] B | `ailx-models` stores/consumes OCI/ModelPack artifacts instead of a custom format |
| T3 | **composefs + fs-verity (+ UKI)** | Verified boot chain for bootc-style images; same mechanism can make a **read-only, verified model store** with no runtime cost per read (hypothesis) | [V-15] A, [V-16] B (in progress upstream) | Image track target; model-store option (H-D-10, new) |
| T4 | **Model files as untrusted input** | Pickle-based checkpoints execute code; parsers of binary model formats are attack surface (PK: past GGUF parser CVEs in llama.cpp); RamaLama already runs models rootless, read-only, without network [L-24] | [L-24] B; rest PK | Runtimes run sandboxed with read-only model mounts; safetensors/GGUF only by default; pickle loading = `explicit-opt-in` |
| T5 | **Agent sandboxing primitives** (Landlock, seccomp, user namespaces/bubblewrap, gVisor, microVMs) | Agents execute generated code; isolation cost is a performance question (RT-9) | PK | New component `ailx-sandbox` (Phase 4); benchmark its overhead |
| T6 | **Userspace PGO/BOLT of CPU inference paths** | More plausible gain than kernel PGO for CPU/MoE offload (all time is in userspace kernels) | PK | Hypothesis H-R-10 (new), runtime-bundle build option |
| T7 | **Continuous eBPF profiling** (perf/BPF stack sampling, OpenTelemetry-style profilers) | Explains *why* a knob helps; feeds the knob registry with mechanisms instead of correlations | PK | Telemetry extension, Phase 2 |
| T8 | **CDI / DRA-style device descriptions** | Podman/Kubernetes already describe GPUs this way; reuse avoids a private schema | PK (open question since Phase 0) | Evaluate before freezing the provider schema (Phase 1.1 WP2 note) |

Not adopted as new tracks (low expected value for the target): Thunderbolt/
USB4 clustering, confidential computing, userspace GPU drivers.

## 3. ADR assessment (recommendations; status changes remain a maintainer decision)

| ADR | Assessment | Recommendation | Amendments recorded in the ADR |
|-----|-----------|----------------|-------------------------------|
| 0005 Layer-first | Correct risk ordering; keeps the vision (platform) separate from the vehicle (image). Weakness: no explicit trigger for starting the image track | **Accept with amendment** | Image-track trigger criteria; layer mode must detect missing kernel features (sched_ext, `SCHED_CACHE`) and degrade |
| 0006 Ubuntu baseline | Sound. Hidden variable: HWE kernels will bring `SCHED_CACHE` (≥7.2) into the "stock" baseline mid-LTS | **Accept with amendment** | Baseline pinned to the GA kernel; HWE kernel = new baseline generation; record conflicting tuning daemons |
| 0007 Management not compute | Sound; memory model was dGPU-centric | **Accept with amendment** | Memory domains (discrete, unified, host); UMA devices in scope |
| 0008 Safety tiers | Sound core; missing: multiple-comparison control, probation for delayed failures, coexistence with other tuning daemons, lockdown-incompatible knobs | **Accept after amendments** | FDR control in screening; T1 probation (soak) before persistent adoption; conflict detection; knobs needing debugfs/MSR are never auto-applied |
| 0009 Licensing | Technically coherent; scx-derived userspace is GPL-2.0-only [V-17]; repo still unlicensed | **Keep Proposed until legal review**; decide before first code merge | Note on GPL inheritance from scx |
| 0010 Deferred validation | Sound; validation must reference the harness version (a buggy harness invalidates results) | **Accept with amendment** | Ledger rows record harness version and A/A calibration ID |

## 4. Architecture boundaries, extensibility, hardware independence

| Finding | Severity | Fix applied |
|---------|----------|-------------|
| Capability model assumed discrete VRAM | High | Memory domains in `hardware-abstraction.md`; UMA provider notes |
| No defined service plane for applications/agents | Medium | Service plane (OpenAI-compatible + native API via reused gateway) and `ailx-sandbox` added to `overview.md` |
| Model store format unspecified; integrity not addressed | Medium | OCI/ModelPack + digest verification + optional fs-verity |
| Coexistence with `power-profiles-daemon`, TuneD, irqbalance, thermald, desktop power managers | Medium | Conflict detection and "yield" rule in `self-optimization.md` |
| Lockdown makes debugfs/MSR knobs unusable on Secure Boot systems | Medium | Registry field `requires`: knobs needing lockdown off are `explicit-opt-in` and never auto-applied |
| Language independence of performance-critical parts | Medium | JSON Schema as canonical contract; samplers speak a line-based protocol (Phase 1.1 spec) |
| NPU/accelerator classes beyond GPUs | Low (no hardware) | Unchanged; capability = supported model families/compilers |

## 5. Benchmark methodology, noise and quality gates

| Issue | Risk | Fix (in `benchmarking.md`) |
|-------|------|---------------------------|
| Screening many knobs inflates false positives | False adoptions | Benjamini–Hochberg FDR in screening; confirmatory re-test with fresh data (already required) |
| Analysis choices made after seeing data | p-hacking | Pre-registered analysis plan in the campaign manifest (primary metric, E_min, α, stopping rule) |
| Variable output length drives throughput noise | Noise masquerading as effect | Fixed output length (EOS ignored) for throughput/latency workloads; natural lengths only in trace-replay workloads, with length distribution recorded (H-S-10, new) |
| GPU boost depends on temperature; heat soak | Drift | Record clocks/temps per sample; fixed cooldown policy; thermal steady-state criterion |
| Required sample size unknown | Under-powered campaigns | Power analysis from A/A variance before each campaign |
| KLD reference for multi-engine comparisons | Ambiguous reference | Reference logits per engine at reference precision; cross-engine comparisons use task-level gates |
| Video/training quality gates immature | Silent quality loss | Labeled as provisional; perf-only claims for video until a gate is calibrated |

## 6. Security, licensing, maintainability

- **Untrusted model files** (T4) and **agent code execution** are the two
  largest new attack surfaces; both are now explicit in the architecture.
- **Privileged daemon** (`ailxd`): unchanged design (narrow IPC, polkit,
  journal) — keep it minimal; it must not load plugins in-process.
- **Licensing**: GPL-2.0-only applies to scx-derived userspace loaders, not
  only to BPF programs [V-17]; plan a separate directory/package boundary.
- **Maintainability**: the long-term cost driver is the runtime-bundle ×
  backend × host matrix. Recommendation: at most one tier-1 host, two
  tier-1 bundles (llama.cpp, vLLM) and one vendor (NVIDIA) until Phase 3; all
  others best-effort.

## 7. Suitability by workload class

| Workload | Fit of current architecture | Main gap → fix |
|----------|-----------------------------|----------------|
| LLM inference/serving | Strong | — |
| Image generation | Medium | VRAM co-residence (VRAM broker, P2); LPIPS thresholds need A/A calibration |
| Video generation | Medium–weak | Long runs, offload-heavy, immature quality metrics → perf-only claims until a gate exists |
| Audio (ASR/TTS, voice) | Medium | Missing realtime class → added (`overview.md`): bounded latency, protected CPU share |
| Training / fine-tuning | Medium | Preemption by interactive work needs checkpoint/resume; loss-curve gate; MLPerf Training rules unreviewed |
| Agents | Medium | Sandbox and service plane were missing → added; prefix caching and admission control already planned |
| Embeddings / RAG | Strong (CPU and GPU) | — |

## 8. Changes made in this review

- Corrected: `02-kernel-and-os.md` (sub-scheduler version, dmem reclaim,
  `SCHED_CACHE` window, H-K-01), `image-and-delivery.md`, `CURRENT.md`.
- Extended: `overview.md`, `hardware-abstraction.md`, `self-optimization.md`,
  `benchmarking.md`, `hardware-test-plan.md` (E3), ADRs 0005–0010 (review
  sections), `state/*` (hypotheses, risks, open questions, roadmap, status).
- New: this document; `docs/architecture/phase1.1-measurement-foundation.md`.

## Sources

| ID | Title | URL | Date | Accessed | Grade | Notes |
|----|-------|-----|------|----------|-------|-------|
| V-01 | Linux `kernel/sched/fair.c`, `kernel/sched/debug.c` (SCHED_CACHE heuristics, debugfs knobs) | https://github.com/torvalds/linux/blob/master/kernel/sched/fair.c | master @ 7.4-rc4 | 2026-11-26 | A | Read via raw.githubusercontent.com |
| V-02 | Linux `init/Kconfig` at v7.1/v7.2/v7.3/master; `kernel/sched/topology.c` | https://github.com/torvalds/linux/blob/v7.2/init/Kconfig | tags | 2026-11-26 | A | Presence per tag |
| V-03 | Linux `init/Kconfig` (`EXT_SUB_SCHED`) at v7.0/v7.1 | https://github.com/torvalds/linux/blob/v7.1/init/Kconfig | tags | 2026-11-26 | A | |
| V-04 | `Documentation/scheduler/sched-ext.rst`; `kernel/sched/ext/ext.c` | https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-ext.rst | master | 2026-11-26 | A | Fallback, watchdog |
| V-05 | `kernel/cgroup/dmem.c` (v7.2 vs v7.3); TTM/xe/amdgpu/nouveau dmem users | https://github.com/torvalds/linux/blob/v7.3/kernel/cgroup/dmem.c | tags | 2026-11-26 | A | |
| V-06 | `kernel/bpf/verifier.c` struct_ops GPL check | https://github.com/torvalds/linux/blob/master/kernel/bpf/verifier.c | master | 2026-11-26 | A | |
| V-07 | `kernel/Kconfig.preempt` | https://github.com/torvalds/linux/blob/master/kernel/Kconfig.preempt | master | 2026-11-26 | A | |
| V-08 | `drivers/platform/x86/amd/x3d_vcache.c` | https://github.com/torvalds/linux/blob/master/drivers/platform/x86/amd/x3d_vcache.c | master | 2026-11-26 | A | default `frequency` |
| V-09 | NVIDIA GPUDirect Storage release notes: known issues / support | https://docs.nvidia.com/gpudirect-storage/release-notes/known-issues.html | n.d. | 2026-11-26 | A | Search snippet only (host blocked) |
| V-10 | Chips and Cheese: AMD's Zen 4, Part 3: System Level Stuff | https://old.chipsandcheese.com/?p=11867 | 2022 | 2026-11-26 | C | Snippet; per-CCD link widths |
| V-11 | AMD ROCm docs: Strix Halo / RDNA3.5 system optimization | https://rocm.docs.amd.com/en/latest/how-to/system-optimization/strixhalo.html | n.d. | 2026-11-26 | A | Snippet; TTM limits, ≥6.18.4 |
| V-12 | Jeff Geerling: Increasing the VRAM allocation on AMD AI APUs under Linux | https://www.jeffgeerling.com/blog/2025/increasing-vram-allocation-on-amd-ai-apus-under-linux/ | 2025 | 2026-11-26 | C | Snippet |
| V-13 | CNCF blog: Advancing AI model interoperability with Docker and ModelPack | https://www.cncf.io/blog/2026/08/12/advancing-ai-model-interoperability-with-docker-and-modelpack/ | 2026-08-12 | 2026-11-26 | B | Snippet; Sandbox since 2025-05-13 |
| V-14 | Docker blog: Why Docker chose OCI artifacts for AI model packaging | https://www.docker.com/blog/oci-artifacts-for-ai-model-packaging/ | n.d. | 2026-11-26 | B | Snippet |
| V-15 | Fedora change: Enabling composefs by default for Atomic Desktops | https://www.fedoraproject.org/wiki/Changes/ComposefsAtomicDesktops | n.d. | 2026-11-26 | A | Snippet |
| V-16 | bootc PR #1706 (composefs backend / sealed images); FOSDEM 2025 UKI + composefs | https://github.com/bootc-dev/bootc/pull/1706 | 2025–2026 | 2026-11-26 | B | Snippet; work in progress |
| V-17 | sched-ext/scx LICENSE; `scheds/rust/scx_layered/Cargo.toml` | https://github.com/sched-ext/scx/blob/main/LICENSE | main | 2026-11-26 | A | GPL-2.0 / GPL-2.0-only |
| V-18 | ROCm Core SDK 10.0.0 release notes | https://rocm.docs.amd.com/en/docs-10.0.0/about/release-notes.html | 2026-08-26 | 2026-11-26 | A | Snippet |
