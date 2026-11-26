# Open Questions and Contradictory Evidence

> Status: Phase 0 · Last updated: 2026-11-26 · Detailed lists live in each research document ("Open questions", "Contradictory or weak evidence"); this file consolidates the decision-relevant ones

Legend: **[HW]** answerable by measurement on the test hardware · **[DOC]**
answerable by reading primary sources once network access allows ·
**[LEGAL]** needs legal review · **[DECIDE]** maintainer decision.

## A. Decisions needed from maintainers

| # | Question | Context |
|---|----------|---------|
| A1 | Accept, amend or reject the proposed ADRs 0005–0010 | `docs/adr/` |
| A2 | Licence set (ADR-0009) — commission legal review; until then the repo has no licence | [LEGAL][DECIDE] |
| A3 | Project name/trademark ("AI Linux" is a working name) | [LEGAL][DECIDE] |
| A4 | Budget for measurement hardware: logging wall meter (ideally SPEC PTDaemon-supported), optional inline GPU power measurement, ambient sensor | [DECIDE]; needed for H-S-01, H-S-07 |
| A5 | Implementation language(s) for Phase 1 (proposal: Python for harness/analysis because the AI tooling ecosystem is Python; revisit Rust for the privileged daemon in Phase 2) | [DECIDE] |
| A6 | Communication platform and Code of Conduct version | [DECIDE] |

## B. Kernel and OS

| # | Question | Type |
|---|----------|------|
| B1 | Do stock Ubuntu 26.04 / Debian 13 / Fedora 45 kernels enable `CONFIG_SCHED_CLASS_EXT` and package `scx`? | [DOC][HW] |
| B2 | Effect of `SCHED_CACHE` (≥7.2) on CPU inference on 2-CCD Ryzen; will a non-debugfs knob be accepted upstream? (H-K-01) | [HW] + upstream |
| B3 | Under Secure Boot lockdown, which scx loaders, bpftrace scripts and power tools work? | [HW][DOC] |
| B4 | Is dmem reclaim merged; does NVIDIA's out-of-tree driver register dmem regions? | [DOC] |
| B5 | Which kernel becomes the 2026 LTS? | [DOC] |
| B6 | Can NPUs (amdxdna, ivpu) be accounted/scheduled alongside GPUs (fdinfo, dmem, cgroups)? | [DOC][HW] |

## C. Hardware, I/O and energy

| # | Question | Type |
|---|----------|------|
| C1 | Which logical CPUs are on the V-Cache CCD; real per-CCD and dual-CCD DRAM read bandwidth at the configured memory profile (H-H-01) | [HW] |
| C2 | NVML energy counter availability/update rate and `power.draw` averaging on Ada GeForce (H-S-07/H-H-08) | [HW] |
| C3 | Zen 4 client RAPL accuracy vs wall meter; RAPL domains on Rocket Lake | [HW] |
| C4 | ReBAR BAR1 size on the 4090; whether cuFile ever leaves compat mode on GeForce | [HW] |
| C5 | Does OpenVINO/Level Zero work for UHD 750 in LXC on the Proxmox 7.0 kernel? | [HW] |
| C6 | Is the Proxmox host quiet enough for CPU-only benchmarks or only fit as controller? | [HW] (A/A) |
| C7 | ZFS (ARC + page cache) double caching for mmap'd models | [DOC][HW] |

## D. Runtimes and quality

| # | Question | Type |
|---|----------|------|
| D1 | Same-build comparison of llama.cpp CUDA/Vulkan, ExLlamaV3, vLLM on the 4090 with KLD-matched quants (H-R-01/02) | [HW] |
| D2 | KLD / same-top-p / flip-rate thresholds that track user-visible quality, per model family; stable across engines? | [HW] research |
| D3 | Cost of batch-invariant (deterministic) kernels — sources disagree (~20% to 2×) | [HW][DOC] |
| D4 | MPS on GeForce: supported/stable with current drivers? | [DOC][HW] |
| D5 | Cheapest quality-eval suite per model variant that is still sensitive enough (KLD corpus size, NIAH/RULER, image/video metrics) | [HW] |
| D6 | Consumer AMD/Intel support status in vLLM/SGLang (ROCm 10, XPU) | [DOC] |
| D7 | Licences of candidate bundled components (e.g. Unsloth Studio AGPL, Modular Community License, TTS engines) | [LEGAL] |

## E. Self-optimization and benchmarking

| # | Question | Type |
|---|----------|------|
| E1 | Which Tier-R knobs matter at all? Screening design (Plackett–Burman/Morris) before BO | [HW] |
| E2 | Drift correction over long campaigns (ambient, firmware updates); daily canary reference runs? | [HW] method |
| E3 | Objective weighting for concurrent workloads: fixed SLO classes or user-declared priorities? | [DECIDE] after HW data |
| E4 | Fine-tuning/training benchmark methodology (MLPerf Training rules not reviewed) | [DOC] |
| E5 | Soak duration and OOM-policy validation needed to bound rare-failure risk | [HW] method |

## F. Delivery, image and legal

| # | Question | Type |
|---|----------|------|
| F1 | Current CUDA 13.x EULA redistributable list; NVIDIA userspace redistribution terms | [LEGAL][DOC] |
| F2 | How do community bootc derivatives (e.g. Universal Blue) ship signed NVIDIA kmods? (prior art for O-D-02) | [DOC] |
| F3 | EU CRA role (manufacturer / steward / out of scope) and obligations | [LEGAL] |
| F4 | Ubuntu trademark/derivative policy for an Ubuntu-based image | [LEGAL] |
| F5 | Sigstore/cosign, OpenSSF Scorecard, KIWI, debos, archiso evaluation (not researched) | [DOC] |
| F6 | NixOS reproducibility status, lanzaboote maturity, 26.11 status | [DOC] |

## G. Contradictory evidence (recorded, unresolved)

| Topic | Conflict | Sources |
|-------|----------|---------|
| InstructLab in RHEL AI 3 | "based on InstructLab" vs "dropped instructlab" | [L-01] vs [L-04] |
| Ollama engine | own engine vs GGUF "through llama.cpp" again | [L-24] vs [L-47]; [R-13] |
| LithOS gains | preprint vs camera-ready figures differ | [L-65] |
| Linux 6.18 LTS EOL | Dec 2027 vs Dec 2028 | [D-54][D-55] |
| CRA steward obligations start | Dec 2027 vs Sep 2026 | [D-89][D-90][D-91] |
| Apache-2.0 ↔ GPLv2 | one source claims one-way compatibility; kernel and FSF say incompatible | 05 §Contradictions |
| `preempt=` options on x86 | build options reduced in 2026 vs boot docs still list NONE/VOLUNTARY | [K-05][K-06] |
| AutoFDO kernel gains | 2–6% vs "up to 20%" | 02 §Contradictions |
| Rocket Lake GVT-g | Intel matrix lists it vs Intel community says unsupported | [H-24][H-25] |
| Rocket Lake in compute-runtime | disabled in 24.39 (press) vs listed as supported (README) | [H-27][H-28] |
| GDS P2PDMA minimum kernel | 6.2 vs 5.14 vs 7.2 (different scopes) | 03 §Contradictions |
| Per-CCD read ceiling | 64 vs 59.7 GB/s | 03 §Contradictions |
| Batch-invariant kernel cost | ~20% vs ~2× vs 63% matmul slowdown | [S-65] et al. |
| CodeCarbon accuracy | 80–96% of wattmeter vs up to 40% error | [S-33][S-34] |
| SageAttention / TeaCache | "no quality loss" claims vs later degradation reports | 04 §Contradictions |
| Vulkan vs CUDA gap (llama.cpp) | large (older data) vs small (newer anecdotes) | 04 §Contradictions |
| AMD NPU kernel requirement | Linux 7.0 vs ≥6.14 | 04 §Contradictions |

## H. Verification debt from Phase 0 access limits

Facts marked **PK** (prior knowledge) or seen only as search snippets must be
re-verified against primary sources before they support an ADR acceptance
(RK-17). Priority: everything cited by ADRs 0005, 0006, 0009 and by the
hardware test plan §1.
