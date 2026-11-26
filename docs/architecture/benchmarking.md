# Benchmark Methodology (Ubuntu Baseline)

> Status: Phase 0 proposal, amended by Phase 0.1 review (no measurements yet) · Last updated: 2026-11-26 · Related ADRs: 0003, 0006, 0008 · Evidence: `docs/research/06-self-optimization-and-benchmarking.md` [S-*], `docs/research/04-runtimes-and-workloads.md` [R-*], `docs/research/03-hardware-io-and-model-loading.md` [H-*]

## Summary

- Every claim AI Linux makes is a **comparison against stock Ubuntu LTS on the
  same hardware**, with the **same model files and the same engine builds**, so
  that differences come from the OS/platform layer — not from different
  software versions.
- Four comparison arms separate *where* a gain comes from: stock Ubuntu,
  Ubuntu with tuned runtime flags, Ubuntu with tuning anyone can apply (TuneD
  profile), and AI Linux. Only `AI Linux − U2` is attributable to the platform.
- Statistics follow the benchmarking literature: randomized interleaved trials,
  A/A noise calibration, multi-level repetition, effect-size confidence
  intervals, and MLPerf-style minimum run lengths for tail metrics
  [S-37][S-46][S-48][S-49][S-50].
- **Quality is measured in every run**, not assumed: KL divergence against
  stored reference logits for LLMs, fixed-seed perceptual distance for images,
  WER for ASR, retrieval scores for embeddings [S-62][S-67][S-69][S-70].
- **Energy ground truth is an external wall meter**; GPU/CPU software counters
  are used only for calibrated attribution [S-29][S-30][S-33][S-36].
- All metric definitions are pinned in this repository because tools define
  TTFT/ITL differently [S-58].
- Thresholds in this document (e.g. minimum effect 2%) are **initial policy
  values to be calibrated** in Phase 1, not evidence.

## 1. Comparison arms

| Arm | OS | System tuning | Runtime configuration | Purpose |
|-----|----|---------------|----------------------|---------|
| **U0** | Ubuntu LTS, stock install, archive kernel and NVIDIA driver | none | engine defaults | "What users get today" |
| **U1** | as U0 | none | best runtime flags found by the T0 tuner (portable, OS-independent) | Separates runtime-flag gains from OS gains (H-L-01) |
| **U2** | as U0 | TuneD `throughput-performance` / `latency-performance` as appropriate [S-02] | as U1 | "Tuning anyone can do" on a mainstream distro |
| **A** | AI Linux (layer mode on Ubuntu first; image later) | AI Linux profile | as U1 (plus platform-level runtime features) | Platform result |

Rules:

- Engine builds, container images and model files are **bit-identical** across
  arms (digests and hashes recorded). Where an arm needs a different build
  (e.g. distro-specific CUDA), the difference is declared and the comparison
  labeled.
- Headline claim = `A` vs `U2`, with `A` vs `U0` reported for context.
- The baseline release is the current Ubuntu LTS at the time of measurement
  (26.04 LTS at the time of writing [L-12][L-15]); version, kernel and driver
  are recorded, and the baseline is re-measured whenever it changes (ADR-0006).
- Distro bake-off arms (Fedora, Arch/CachyOS, Debian, NixOS — see
  `image-and-delivery.md`) use the same protocol with the same runtime bundles.

## 2. Workload suite v0 (classes; concrete models pinned at Phase 1 start)

Model choice is deferred to the first manifest version because the model
landscape changes monthly; selection criteria: open weights with a license that
permits benchmarking and local use, popular, fits the hardware class, reference
precision available. Each workload is a versioned manifest (model hash,
dataset hash, parameters, engine bundle digest).

| ID | Class | Description | Engine(s) | Primary metric | Why |
|----|-------|-------------|-----------|----------------|-----|
| W-LLM-1 | LLM, single user | Dense ~8B at reference precision (BF16, fits 24 GB) and labeled lossy variants (Q8, ~4-bit) | llama.cpp, vLLM | decode tok/s (tg), prefill tok/s (pp), TTFT | Bandwidth-bound decode; quant comparison with KLD |
| W-LLM-2 | LLM, near VRAM limit | Dense ~27–32B at ~4–5 bpw | llama.cpp, ExLlamaV3 (opt.) | decode tok/s, TTFT | H-R-01 engine comparison |
| W-LLM-3 | LLM, MoE with CPU offload | MoE whose weights exceed VRAM but fit VRAM+RAM with ≥8 GB headroom | llama.cpp (expert offload), KTransformers (opt.) | decode tok/s, TTFT | CPU/RAM/scheduler path — where OS knobs are most likely to matter |
| W-SRV-1 | LLM serving | W-LLM-1 model under Poisson load, sweep to goodput | vLLM `bench serve`/GuideLLM [S-14][S-59] | goodput at SLO class | Batched serving |
| W-AGT-1 | Agent trace replay | Multi-turn tool-calling traces with shared prefixes, concurrent sessions | vLLM/SGLang, llama.cpp server | p99 TTFT, goodput | Prefix caching, speculation (O-R-03/04) |
| W-EMB-1 | Embeddings | Batch embedding of fixed corpus; GPU and CPU | TEI/llama.cpp/ONNX Runtime | docs/s; nDCG@10 for quality | CPU path also on i5-11500 |
| W-ASR-1 | Speech-to-text | Fixed audio set | whisper.cpp / faster-whisper | real-time factor; WER | CPU and GPU |
| W-IMG-1 | Image generation | Diffusion model, fixed seeds/resolution/steps, "reference" preset | ComfyUI or diffusers | s/image, peak VRAM; LPIPS/CLIP | Non-LLM GPU path |
| W-VID-1 | Video generation | Small video model (e.g. a ~5B text/image-to-video model stated to fit 24 GB [R-59]), short clip | ComfyUI/diffusers | s/clip, peak VRAM | Offload & memory pressure |
| W-FT-1 | Fine-tuning | QLoRA on ~8B, fixed dataset & steps | Unsloth or TRL+PEFT | tokens/s, peak VRAM; loss curve equality | Training path |
| W-LOAD-1 | Model loading | Load S/M/L models (safetensors and GGUF), cold and warm | vLLM, llama.cpp | time-to-ready split into read / deserialize / upload / warmup | I/O path (O-H-*) |
| W-MIX-1 | Concurrency | W-LLM-1 interactive + W-IMG-1 batch on one GPU | as above | interactive p99 TTFT, batch throughput, OOM count | GPU sharing (O-R-06) |
| W-MIX-2 | Concurrency | W-LLM-3 + CPU-heavy background (compile/stress-ng) | as above | decode tok/s, p99 ITL | CPU scheduler isolation (sched_ext) |
| W-MIX-3 | Concurrency | Interactive LLM + W-FT-1 | as above | interactive p99, fine-tune throughput | Background training |
| W-SOAK-1 | Stability | 24 h mixed workload loop | all | errors, OOMs, throttling, Xid, drift | Stability gate |

Suite tiers: **smoke** (≈10 min; every change), **standard** (≈2 h; every
candidate profile), **full** (overnight incl. soak; releases and T2/T3 changes).
Durations are targets to be adjusted after Phase 1 calibration.

## 3. Metrics (pinned definitions)

| Metric | Definition | Notes |
|--------|------------|-------|
| TTFT | Request send → first generated token received by client | Reasoning models: also record time to first *output* token (TTFO) [S-58] |
| TPOT | (E2E − TTFT) / (output tokens − 1) per request | Mean per request, then distribution across requests |
| ITL | Distribution of gaps between consecutive tokens, **excluding** TTFT | Tools differ on this; adapters normalize [S-58] |
| E2E | Request send → last token | |
| Throughput | Output tokens/s (and total tokens/s) over the steady-state window | Report prefill and decode separately for single-user runs (pp/tg) |
| Goodput | Max request rate at which ≥90% of requests meet both TTFT and TPOT SLO [S-57] | SLO classes borrowed from MLPerf interactive/conversational limits [S-37] |
| Load time | Process start → ready to serve, split into storage read, deserialization, host→device upload, warmup/autotune | Cold = after `sync; echo 3 > /proc/sys/vm/drop_caches` [S-45]; warm = second load |
| Memory | Peak and steady RSS, page cache attributable to model files, peak VRAM (driver-reported) | |
| Energy | Joules per output token (prefill and decode separately), per image, per request at fixed SLO; average and peak wall power | Ground truth: wall meter; attribution: NVML energy counter, RAPL |
| Quality | See §5 | Mandatory in every standard/full run |
| Stability | Count of: kernel warnings, GPU driver errors (NVIDIA Xid / amdgpu resets), OOM kills, sched_ext ejections, thermal/power throttle events, crashes | Any non-zero in a candidate run fails the stability gate |

## 4. Protocol and statistics

**Environment control** (recorded in the Environment Fingerprint, see
`overview.md`):

- BIOS/firmware versions and settings (memory profile/EXPO, PBO/boost, CPPC,
  C-states, Resizable BAR, PCIe link speed), microcode, kernel + cmdline,
  driver and CUDA/ROCm versions, engine commit, model hashes.
- CPU governor/EPP, boost state, GPU power limit, clocks policy, persistence
  mode, temperatures, fan policy, ambient temperature (sensor), room power.
- Automatic updates disabled during runs (e.g. `unattended-upgrades`), no
  interactive desktop activity, network quiesced (only the controller link).
- GPU clocks are **not locked** for headline runs (users do not lock them);
  locked-clock runs may be used for micro-benchmarks and are labeled.
- Setup factors not under test are randomized where feasible (environment
  size, launch order) to avoid measurement bias [S-47][S-52].

**Repetition design** (after Kalibera & Jones [S-48]):

- Levels: iterations within a process → process restarts → reboots (for
  boot-level changes) → reinstall (only for distro bake-off).
- Warmup iterations are fixed per workload and reported separately (MLPerf
  does not define warmup [S-37], so we must).
- Dynamic run count: continue until the relative standard deviation is below a
  threshold or a maximum is reached (PTS-style [S-56]); tail metrics need
  ≥600 s per run (MLPerf minimum) [S-37].

**A/B execution**: randomized multiple interleaved trials (RMIT) — e.g.
ABBA-ordered or randomized blocks — so that periodic interference and drift
cancel [S-50]. Boot-level comparisons interleave reboots into A and B.

**A/A calibration**: before any A/B campaign on a machine, run A/A campaigns to
estimate the noise floor per workload/metric; the harness is validated by
checking that A/A comparisons report a "significant" difference no more often
than the configured false-positive rate.

**Reporting**: median with 95% bootstrap CI, p50/p90/p99, and the **effect size
with CI** (ratio of medians or Hodges–Lehmann shift) [S-48][S-49]. Non-normal
data uses nonparametric methods (Mann–Whitney U / bootstrap) [S-49].

**Pre-registration (Phase 0.1 review)**: every campaign manifest fixes,
before the first run, the hypothesis IDs, arms, primary and guard metrics,
E_min, α, multiple-comparison policy, stopping rule and the RMIT seed
(schema `campaign.v0`, `phase1.1-measurement-foundation.md`). Changing the
plan after data exists creates a new campaign.

**Multiple comparisons**: exploratory screening campaigns control the false
discovery rate (Benjamini–Hochberg); confirmatory campaigns test one
pre-registered primary metric per hypothesis.

**Power**: the number of runs per arm is derived from the A/A variance of the
primary metric and E_min (simulation-based) before the campaign starts.

**Output-length control**: throughput and latency workloads use a fixed
output length (EOS ignored, achieved length verified); natural output lengths
are used only in trace-replay workloads (W-AGT-1), whose length distribution
is recorded (H-S-10).

**Thermal and boost control**: GPU/CPU clocks and temperatures are recorded
with every sample; a fixed cooldown policy between runs and a thermal
steady-state criterion before the measurement window; runs that throttle are
flagged.

**Significance policy (initial, to be calibrated — H-S-03)**: a difference is
*claimed* only if the CI lower bound exceeds max(2%, 2 × A/A noise floor), where the
noise floor is the absolute A/A effect point estimate (systematic offset between
identical arms; definition revised in Phase 1.1, see `phase1.1-measurement-foundation.md` §8). Smaller
or inconclusive results are reported as "no detectable difference" — which is a
legitimate, publishable outcome.

## 5. Quality gate

| Modality | Reference | Test | Gate for "lossless" knobs | Gate for lossy options (opt-in) |
|----------|-----------|------|---------------------------|----------------------------------|
| LLM | Stored reference logits from the reference precision on a fixed corpus | Mean KLD (with 16-bit storage floor subtracted), top-token agreement, Δp percentiles [S-62]; answer flip rate on a fixed task subset [R-24]; long-context retrieval (NIAH/RULER-type) for KV-cache-related knobs (H-R-03) | KLD and flip rate statistically indistinguishable from the A/A distribution at the same precision | Measured deltas shown to the user; never auto-adopted |
| LLM (periodic) | Published or reference scores | lm-evaluation-harness subset [S-66] | No significant drop | Shown |
| Image/video | Fixed-seed outputs from reference preset | LPIPS vs reference, CLIP score [S-69][S-70]; FID only for release checks [S-68] | Within A/A spread (GPU kernels may be nondeterministic) | Shown |
| ASR | Fixed audio set with transcripts | WER | No significant WER increase | Shown |
| Embeddings | Fixed retrieval task subset [S-67] | nDCG@10 / recall@k | No significant drop | Shown |
| Fine-tuning | Reference run | Loss curve and eval loss at fixed steps | Within run-to-run spread | Shown |

Notes:

- Exact-match comparisons are **not** used as gates for batched serving because
  batch-dependent kernels are not batch-invariant [S-63][S-64]; a
  deterministic/batch-invariant mode may be used for debugging (O-R-14).
- Engine/kernel updates are themselves quality risks (a FP8 attention bug
  dropped long-context retrieval sharply in one engine release [R-07]); the
  quality gate therefore also runs on every runtime-bundle update.

## 6. Energy measurement

- **Ground truth**: external wall meter with logged samples, measurement
  boundary declared (whole system at the wall, monitor excluded) following
  MLPerf Power practice [S-36]. Meter model to be chosen (open question;
  preference for a SPEC PTDaemon-supported meter).
- **Attribution**: NVML energy counter (preferred over sampled power, which may
  miss short peaks [S-29]); RAPL package energy for CPU (with known accuracy
  limits on AMD [S-30]); calibrated against the wall meter per machine.
- **Idle baseline**: measured and reported separately; energy per token is
  reported both gross and idle-subtracted.

## 7. Results store and publication

- Every run produces a **Result Record** (see `overview.md` data model) with
  raw samples, the full fingerprint and manifests; results are immutable and
  addressed by ID.
- Reports are generated from result IDs only; any number in docs, release notes
  or PRs must link a result ID (ADR-0003).
- Raw data and analysis scripts are published with reports (opt-in for
  community submissions; no automatic upload).

## 8. Validating the harness itself

1. **A/A tests**: false-positive rate matches the configured level.
2. **Known-effect injection**: deliberately degrade a configuration (e.g. lower
   GPU power limit, restrict CPU cores) and verify the harness detects the
   effect with the expected sign and plausible magnitude.
3. **Cross-tool consistency**: same workload through two tools (e.g. vLLM bench
   and GuideLLM) yields consistent metrics after definition normalization.
4. **Reproducibility check**: a second operator re-runs a published result
   from manifests and obtains overlapping CIs.

## Open items

Meter selection, quality thresholds per modality, drift correction for long
campaigns, MLPerf Training-style rules for fine-tuning, and the soak/OOM
methodology are tracked in `docs/state/open-questions.md`.
