# Self-Optimization and Benchmark Methodology

> Status: Phase 0 research · Last updated: 2026-11-26 · Evidence grading: see 00-method.md

## Summary

- **Nothing off the shelf does the whole job, but most of the parts exist.** No tool benchmarks candidate system configurations for AI workloads, scores them, adopts winners and rolls back regressions. TuneD offers static profiles [S-01][S-02]. bpftune tunes networking continuously and backs off when an administrator overrides it [S-04]. AutoOS and Wayfinder search kernel *build* configurations, which needs a rebuild and reboot per trial and was scored on general benchmarks (UnixBench) [S-06][S-09][S-75]. AI Linux should reuse these as actuators and as design patterns. Its own decision loop should be new.
- **Measurement noise, not search strategy, is the main way auto-tuning fails.** TUNA reports that 39.0 % of configurations seen during tuning were "unstable" (the source's claim) [S-10]. Mytkowicz et al. showed that innocuous setup changes (environment size, link order) bias results enough to reverse conclusions [S-47]. The optimizer must treat every benchmark as a noisy, possibly biased oracle. That means randomized interleaved trials [S-50], A/A noise calibration and re-validating a winner before adopting it.
- **Inference-engine tuners already exist; orchestrate them rather than rewriting them.** vLLM's `auto_tune.sh` grid-searches `max-num-seqs` × `max-num-batched-tokens` under a P99 end-to-end latency cap [S-13]. `vllm bench sweep` runs configuration grids and SLA searches [S-14]. llama-bench sweeps comma-separated parameter lists with repetitions [S-18]. NVIDIA AIConfigurator predicts configurations from per-operation profiles, but its data is validated only on datacenter GPUs [S-16].
- **The safe-adoption building blocks are mature and upstream.** systemd boot counting with `boot-complete.target` [S-39], greenboot-rs health checks for bootc and rpm-ostree [S-40], `bootc rollback` (reorders deployments) [S-43], and sched_ext, which falls back to the fair scheduler on error, stall or SysRq-S [S-44]. This supports a two-tier model: knobs reversible at runtime (seconds) and image-level changes (reboot plus boot assessment).
- **Detect regressions at two levels.** For each adoption decision, use an A/B test reported as an effect-size confidence interval [S-48][S-49]. Over the long run, apply E-Divisive change-point detection to time series. It is proven in MongoDB's CI and now lives on as Apache Otava (formerly Hunter) [S-53][S-54][S-55].
- **LLM latency metrics are defined differently by different tools.** GenAI-Perf excludes TTFT from the ITL average while LLMPerf includes it [S-58]. AIPerf separates TTFT from "time to first output" for reasoning models [S-58]. LLMPerf was archived on 2025-12-17 [S-60]. The harness must pin metric definitions and record the tool and its version.
- **Use goodput, not peak throughput, as the main serving objective.** Goodput is the highest request rate that still meets an SLO-attainment target [S-57]. MLPerf Inference gives citable defaults: TTFT/TPOT limits per model, accuracy at 99 % or 99.9 % of the reference, at least 600 s per run, and early-stopping percentile estimates [S-37].
- **Quality gate: KL divergence against stored reference logits** (llama.cpp `--kl-divergence`) is cheap and sensitive, and reports top-token agreement [S-62]. Exact-match gates fail under batched serving because batch-size-dependent kernels are nondeterministic [S-63][S-64]. Sources disagree sharply on how much batch-invariant kernels cost [S-65].
- **Software energy counters cannot be the ground truth.** nvidia-smi samples power for about 25 % of runtime on A100/H100, with an estimated ±5 % error (the source's claim) [S-29]. AMD RAPL has no DRAM domain and under-reports package power relative to external measurement on Zen 2 [S-30]. Software estimators deviate by up to 40 % from an external meter [S-33]. Use a wall meter for headline numbers, and software counters only for calibrated relative comparisons.
- **GPU power capping is the most promising low-risk first target for energy optimization,** and it is fully reversible. Zeus and Perseus give the method [S-26][S-28]. The evidence that consumer cards keep decode speed under a power cap is anecdotal (grade D) [S-71][S-72], so this is hypothesis H-S-01, to be measured on the RTX 4090.

## Scope

This document covers four things. The first is auto-tuning systems at the OS, inference-engine, compiler and generic-optimizer levels. The second is safe adoption and rollback mechanisms. The third is benchmark methodology, from the statistics literature and MLPerf rules. The fourth is how to verify that quality and energy have not been silently degraded. It covers stability and concurrency testing only briefly; see Open questions. It does not cover kernel mechanisms themselves (02), hardware data paths (03) or runtime internals (04). It ends with requirements for two AI Linux components:

1. **Self-optimizer:** proposes candidate configurations from an allow-list of knobs, evaluates them through the harness, adopts winners in stages and rolls back on regression.
2. **Benchmark harness:** a reproducible methodology with stock Ubuntu as the baseline. It measures throughput, latency, model load time, RAM/VRAM, energy, quality, stability and concurrent workloads. First targets are the Ryzen 9 7950X3D + RTX 4090 workstation and the i5-11500 Proxmox server.

## Findings

### F1. OS-level auto-tuners: TuneD and bpftune

- **TuneD** is a profile-based daemon. The latest stable release is v2.28.0 (22 Aug; we infer 2026 from the release sequence). It added bootc loader-entry support and requires rpm-ostree on image-mode systems [S-01]. v2.27 enabled CPU boost in performance profiles and added a `reapply_sysctl_exclude` option [S-01]. v2.26 shipped in August 2025 [S-03].
- `throughput-performance` disables power-saving mechanisms and applies sysctl settings for disk and network I/O throughput. `latency-performance` sets the `performance` governor and holds the CPU in shallow C-states through PM QoS [S-02]. `tuned-ppd` provides a power-profiles-daemon-compatible interface [S-01].
- TuneD v2.24.1 fixed two local privilege and API-parameter vulnerabilities (CVE-2024-52336, CVE-2024-52337) [S-01]. **Any root daemon that applies knobs is attack surface**, and the AI Linux optimizer will be one.
- **bpftune** (Oracle, GPL-2.0) observes the system with BPF and tunes continuously. Its design principles map directly onto our needs [S-04]:
  - It needs no configuration.
  - When an administrator sets a managed tunable, it detects the change and disables that tuner.
  - Every change is logged to syslog with what changed and why.
  - Tuners use "strategies" with a timeout and an evaluation function.
  - TCP buffer tuning is push-pull: sizes grow, then back off under memory pressure. Growth stops once the buffer-size/sRTT correlation exceeds 0.7.
- bpftune's tuners are almost all networking: TCP, IP fragmentation, neighbour and route tables, net/UDP buffers, and netns [S-04]. The GitHub page lists no releases. Phoronix reported a 0.4-1 release in December 2025 [S-05]. **For a single-host AI box, the patterns matter more than the tuners.**

### F2. Research on OS and kernel auto-tuning

- **AutoOS (ICML 2024)** uses an LLM to walk the Kconfig tree as a "dynamic tree", with a state machine that prunes and corrects options so the OS still boots [S-06]. It reports up to 25 % better results than vendor configurations [S-06]; a survey notes the metric was UnixBench [S-75]. **BYOS** adds a Kconfig knowledge graph to reduce hallucinated options [S-07].
- **Wayfinder** began as an automated, reproducible platform for searching OS configurations (APSys '21) [S-08]. The 2026 version (accepted at EuroSys '26 per the preprint) uses neural-network-guided search. It claims up to 24 % better application performance and 8.5 % less memory than default configurations. On Unikraft it reached a tuned configuration in about 100 min, against more than 160 min for Bayesian optimization (the source's claims) [S-09]. For Linux, randomly sampled kernel configurations often failed to build, so the authors pruned the space with Cozart first [S-09].
- **TUNA (EuroSys '25)** reports that 39.0 % of configurations seen during tuning were unstable, with a coefficient of variation of up to 101.3 % on TPC-C (the source's claims) [S-10]. It samples across several nodes and adds an outlier detector so unstable configurations never reach the optimizer's model [S-10]. **DarwinGame** uses tournaments for the same noise problem [S-11]. "Performance Roulette" shows that cloud "weather" distorts ML-based system optimization [S-74].
- **LinnOS (OSDI '20)** runs a lightweight neural network inside the kernel's I/O path to predict SSD latency per request. This shows in-kernel ML is feasible, but each model is trained for a specific device [S-12].
- *Takeaway:* searching kernel build configurations is expensive (rebuild and reboot per trial) and specific to one benchmark. Searching runtime knobs is cheaper and reversible. That is where the self-optimizer should start.

### F3. Inference-engine and compiler configuration tuners

- **vLLM `auto_tune.sh`** [S-13]:
  - It tests every combination of `NUM_SEQS_LIST` × `NUM_BATCHED_TOKENS_LIST` and maximizes throughput.
  - Constraints: `MAX_LATENCY_ALLOWED_MS` (P99 end-to-end latency) and an optional minimum prefix-cache hit rate.
  - Each combination first runs at `--request-rate inf`. If the latency cap is missed, the request rate is lowered until it is met.
  - It searches `gpu-memory-utilization` only on TPU. On CUDA, the value is fixed at 1.0 when that search is skipped.
  - A practical gotcha: it kills processes with `pkill -f vllm`.
- **`vllm bench sweep serve`** runs a configuration grid, three repetitions per combination by default. A `serve_sla` variant searches request rate or concurrency against SLA limits [S-14]; this was seen via search summary only.
- **SLO-Guard** (2026 preprint) treats tuning eight vLLM knobs as a crash-aware problem, because infeasible configurations such as KV-cache overflow crash the server [S-15].
- **AIConfigurator** (NVIDIA) splits inference into operations measured on the target GPU and recombines them to predict end-to-end performance [S-16][S-17].
  - Outputs: a Pareto frontier and deployment artifacts for TensorRT-LLM, SGLang and vLLM.
  - Its data is silicon-validated on H100, H200 and B200 only. The paper itself notes that aggregated serving with chunked prefill often beats disaggregation for small deployments [S-16].
  - Claimed gains: up to 40 % for dense models and 50 % for MoE models (the source's claims) [S-16].
- **llama-bench** [S-18]:
  - Defaults to 5 repetitions (`-r`) and reports mean ± standard deviation in tokens per second.
  - Comma-separated or range values sweep `-ngl`, `-b`, `-ub`, `-t`, `-fa`, `-ctk/-ctv` and `-sm`. `-d` prefills the KV cache to a set depth.
  - Warmup is on by default. `--delay` and `--prio` exist.
  - JSON output keeps the individual repetitions.
  - Results exclude tokenization and sampling time.
- **Compiler autotuning** is already built into the frameworks:
  - Triton's `@triton.autotune` benchmarks a list of configurations, keyed on input-shape arguments, and caches the winner [S-19].
  - Ansor and TVM MetaSchedule search tensor programs with learned cost models [S-20][S-21].
  - *Implication:* the OS should not duplicate this. It should **make autotuning caches persistent and part of the measured state**, because a cold cache changes warmup time.

### F4. Generic black-box optimizers

- **Optuna** [S-22]:
  - v4.4 extended `GPSampler` to multi-objective problems. v4.5 added constrained multi-objective search with a `ConstrainedLogEHVI` acquisition function.
  - v5.0.0 deprecates `constraints_func`, moves constraints into the core Trial API, and makes TPE the default multi-objective sampler.
  - The v4.5 docstring and blog disagree on whether multi-objective constraints are supported. See Contradictory evidence.
- **Ax** reached a stable 1.0 (reported December 2025) [S-23]. Its default multi-objective acquisition function, qNEHVI, is one-step Bayes-optimal for hypervolume in both noisy and noiseless settings [S-24]. Ax supports outcome constraints.
- **SMAC3** uses random-forest surrogates suited to mixed and conditional spaces [S-25]. That fits knob spaces with categorical choices, such as governor or scheduler.
- *Fit for AI Linux:*
  - Trials are expensive (minutes each), the space mixes categorical and continuous knobs, and there are several objectives (performance, latency, energy) plus hard constraints (quality, stability).
  - Bayesian optimization or TPE with outcome constraints fits this setting.
  - Pure grid search is acceptable only for the 2–3 most important serving parameters, as vLLM and llama-bench already do [S-13][S-18].

### F5. Safe adoption and rollback mechanisms

| Mechanism | What it gives | Evidence |
|---|---|---|
| systemd automatic boot assessment | `+tries_left-tries_done` counters in boot-entry file names. `systemd-bless-boot` removes the counters once `boot-complete.target` is reached. `systemd-boot-check-no-failures` fails the boot if any unit failed. An entry at zero tries left is "bad" and sorted after the good ones. Implemented by systemd-boot; other loaders may implement it | [S-39] |
| greenboot / greenboot-rs | Health-check scripts. `required.d` failures trigger rollback; `wanted.d` failures are only logged. The Rust rewrite supports bootc and rpm-ostree (Fedora 43 change). Packages at 0.16.x | [S-40][S-41][S-42] |
| bootc rollback | Reorders boot entries so the previous deployment boots next; a staged deployment is discarded. `/etc` changes made after the previous deployment do not carry over. `--soft-reboot=auto\|required` | [S-43] |
| NixOS generations | Each system build is a selectable boot generation; `nixos-rebuild switch --rollback` | [S-76] |
| btrfs + snapper | Filesystem snapshots before and after changes; rollback by snapshot | [S-77] |
| sched_ext | A BPF scheduler can be loaded and unloaded at runtime. Errors, stalls of runnable tasks, or SysRq-S abort it and return tasks to the fair class. The docs say "system integrity" is kept whatever the BPF scheduler does | [S-44] |
| bpftune pattern | Yields to manual administrator settings and logs every change with its reason | [S-04] |

- *Takeaway:* image-level changes such as kernel version, kernel command line or driver version need an A/B image with boot counting and health checks. Runtime knobs such as sysctl, cpufreq/EPP, THP mode, sched_ext, GPU power limit and engine flags can be reverted within seconds. The optimizer should treat these two tiers differently.

### F6. Benchmark-methodology literature

- **Georges et al. (OOPSLA 2007):** common practice (best-of-N, a mean without a confidence interval) can produce misleading or wrong conclusions. They recommend confidence intervals and ANOVA, and separate methods for start-up and steady-state performance [S-46].
- **Mytkowicz et al. (ASPLOS 2009):** innocuous factors such as UNIX environment size and link order introduce measurement bias large enough to flip conclusions. The remedies are setup randomization and causal analysis [S-47]. **Stabilizer** randomizes memory layout during execution so tests are statistically sound [S-52].
- **Kalibera & Jones (ISMM 2013):** a cost model chooses how many repetitions to run at each level (iteration, execution, build) for a target precision. Results are reported as an **effect-size confidence interval**, demonstrated on DaCapo and SPEC CPU [S-48].
- **Hoefler & Belli (SC '15):** they reviewed 120 papers, found practice lacking, and give 12 rules. These cover reporting variability, checking normality before using parametric confidence intervals, and summarizing data correctly [S-49]. The authors' talk slides give as an example "measured until the 99 % CI was within 5 % of the mean" [S-49].
- **Randomized Multiple Interleaved Trials (RMIT)** (Abedi & Brecht, ICPE 2017): A/A testing showed that periodic interference can masquerade as a real difference between alternatives. Randomizing trial order removes this [S-50]. **Duet benchmarking** runs A and B at the same time so that shared noise cancels [S-51].
- **Phoronix Test Suite:** a "dynamic run count" adds runs while the relative standard deviation exceeds a threshold. The threshold dropped from 3.5 % to 3.0 % (8.8) and then to 2.5 % on new installs (10.2), according to release notes [S-56].

### F7. Benchmark rules: MLPerf

- **MLPerf Inference** rules [S-37]:
  - Scenarios: SingleStream (90th-percentile latency), MultiStream (99th), Server/Interactive (Poisson arrivals; the metric is the highest throughput that meets latency limits) and Offline (throughput).
  - Run length: at least 600 s. Offline also needs at least 24,576 samples.
  - Tail-percentile estimates use **early stopping** at 99 % confidence.
  - Accuracy targets are 99 % or 99.9 % of the FP32/FP16 reference. LLMs also have output-length bounds.
  - Example LLM latency limits: Llama3.1-8B "interactive" allows 500 ms TTFT and 30 ms TPOT; "conversational" allows 2000 ms and 100 ms.
  - Caching of queries, responses or intermediate results is forbidden.
  - Only three kinds of nondeterminism are allowed: floating-point operation order, random input traversal and rounding. Random numbers must come from seeded MT19937.
  - "Results that cannot be replicated are not valid results."
  - The rules say nothing about warmup.
- **MLPerf Client v1.5** (2025-11-18) [S-38]:
  - Adds an *experimental* Linux CLI with Ubuntu 24.04 listed.
  - Metrics: TTFT, and tokens per second excluding the first token.
  - Optional power measurement through MLPerf Power and SPEC PTDaemon.
- **MLPerf Power (HPCA 2025)** [S-36]:
  - Requires a defined measurement boundary and full-system measurement.
  - For edge and inference systems, a SPEC-approved external analyzer (seen via a third-party summary).
  - For large training systems, own telemetry or estimation is allowed.
- MLPerf Training was **not reviewed** in this pass (see Open questions).

### F8. LLM serving metrics and tools

- **Metric definitions:**
  - TTFT: time to first token.
  - TPOT: mean time between generated tokens after the first.
  - ITL: inter-token latency distribution.
  - E2E: request latency.
  - Output-token throughput.
  - Definitions differ across tools: GenAI-Perf leaves TTFT out of the ITL average; LLMPerf includes it [S-58]. AIPerf, the rewrite that replaces GenAI-Perf, separates TTFT from TTFO for reasoning tokens [S-58].
- **Goodput:** the highest request rate at which a target share of requests (for example 90 %) meets both TTFT and TPOT SLOs [S-57]. DistServe reports up to 7.4× more requests, or 12.6× tighter SLOs, from separating prefill and decode (the source's claims; datacenter A100 setting) [S-57].
- **Tool status, 2026-11:**
  - vLLM `bench serve` and `bench sweep` [S-14].
  - GuideLLM, now under vllm-project, with modes synchronous, concurrent, throughput, constant, poisson and sweep. Sweep runs synchronous, then throughput, then constant rates in between [S-59].
  - AIPerf [S-58].
  - HF inference-benchmarker (Rust, JSON output) [S-61].
  - LLMPerf has been **archived since 2025-12-17** [S-60].
  - SGLang's `bench_serving` was not verified.
- **Model load time:** writing 1/2/3 to `drop_caches` frees the page cache, reclaimable slab objects, or both. It does not free dirty objects, so run `sync` first. The kernel warns it "can cause performance problems" outside testing [S-45]. Cold-cache loads (after `sync; drop_caches=3`) and warm-cache loads must be reported separately.

### F9. Quality verification ("no silent quality reduction")

- **llama.cpp KLD workflow** [S-62]:
  - Steps: save reference logits (`--kl-divergence-base`), then run the candidate with `--kl-divergence`.
  - Reported: mean KLD with uncertainty, PPL ratio, mean Δp of the correct token, Δp percentiles, RMS Δp, and "same top p" (top-token agreement).
  - Symmetric Δp percentiles indicate added noise rather than systematic degradation.
  - The reference file is large; the README quotes 37 GiB for LLaMA 3 on Wikitext-2.
  - Example from the README: Llama 3 8B q4_K_M has PPL 6.407 against 6.233 for f16, and mean KLD 0.031273. The f16-vs-f16 row's KLD of 0.000551 comes only from storing logits as 16-bit values, which sets a **measurement floor** [S-62].
- **Nondeterminism:**
  - Thinking Machines (September 2025) argues that run-to-run variation at temperature 0 comes mainly from kernels that are **not batch-invariant**: changing server load changes batch size and therefore reduction order. Floating-point atomics are not the main cause. They demonstrate deterministic vLLM inference with batch-invariant kernels [S-63][S-64]; the primary source was read only via secondary summaries.
  - LLM-42 measured a batch-invariant matmul peaking at 194 TFLOPS against 527 TFLOPS for cuBLAS (the source's claim) [S-65].
  - MLPerf allows only FP-order, traversal and rounding nondeterminism [S-37].
- **Task-level quality checks:**
  - lm-evaluation-harness for LLM task accuracy [S-66].
  - MTEB retrieval subsets for embeddings [S-67].
  - FID [S-68] and CLIPScore [S-69] for image-generation quality, plus LPIPS [S-70] for the perceptual difference between fixed-seed outputs.
  - Word error rate on a fixed audio set for ASR (a standard metric; no specific source reviewed).

### F10. Energy measurement

- **NVML / nvidia-smi:** on A100/H100, power sampling covers only about 25 % of runtime. Error is about ±5 % rather than the ±5 W NVIDIA states, which could be about ±30 W on a 700 W GPU (the source's claims; more than 70 GPUs checked against external meters) [S-29]. Consumer Ada GPUs were not covered in the material we could reach (see H-S-07).
- **RAPL:**
  - On Zen 2, AMD has no DRAM domain, and the package domain reports significantly less than external measurement [S-30].
  - Earlier work compared RAPL and AMD APM against calibrated reference meters [S-31].
  - Raffin & Trystram analyse the mechanics of RAPL-based measurement and release more correct implementations [S-32].
  - No Zen 4-specific validation was found.
- **Estimators:**
  - A 2025 validation found CodeCarbon-style estimates off by up to 40 % compared with an external meter [S-33].
  - A 2023 review measured CodeCarbon at 80–96 % of wattmeter readings, while noting that per-process and whole-machine figures are not comparable [S-34].
  - Kepler v0.10 was rewritten. It is RAPL/powercap-based, with experimental NVIDIA GPU and Redfish support, and attributes only active CPU use [S-35].
  - Scaphandre's current status was not verified.
- **Zeus and Perseus:**
  - Zeus (NSDI '23) characterizes the time–energy trade-off of GPU training through GPU power limit and batch size [S-26].
  - The Zeus library now covers CPU, DRAM, AMD GPUs, Apple Silicon and Jetson [S-27].
  - Perseus (SOSP '24) removes "energy bloat" in pipeline-parallel training: up to 30 % less energy with no throughput loss (the source's claim) [S-28].
  - TokenPowerBench describes energy-per-token methodology based on NVML [S-73].
- **Power capping on consumer GPUs (grade D only):**
  - On an RTX 3090, a 280 W cap saved about 70 W with under 1 % loss for LLM inference, and performance collapsed below about 200 W [S-71].
  - On an RTX 4090, fine-tuning was most efficient at about 50 % power (about 220 W), keeping about 82 % of maximum speed [S-72].

### F11. Stability and concurrency

- This topic received the least direct evidence in this pass. Relevant facts:
  - Prefill and decode interfere with each other when co-located on one GPU [S-57].
  - "Best" configurations can be unstable [S-10].
  - MLPerf requires at least 600 s per run so that tail-percentile estimates are valid [S-37].
- Requirements for the harness (soak duration, OOM behaviour, interference matrices) are therefore given as design proposals and hypotheses, not as cited findings.

## Design implications for AI Linux

**Self-optimizer**

1. **Allow-listed knob manifest (deny by default).** Each knob is declared with:
   - its tier: runtime-reversible (R) or image-level (I);
   - its level: kernel, userspace, runtime or application;
   - its value domain;
   - how it is applied and how it is reverted;
   - its *security impact* and *quality impact* (lossless, lossy or configurable-lossy).

   Knobs that reduce security are never admitted: CPU vulnerability mitigations off, IOMMU off, LSM permissive, lockdown off, unsigned modules, relaxed `perf_event_paranoid` outside profiling sessions. Lossy knobs (quantization level, KV-cache dtype, reduced-precision math modes) require explicit user opt-in and a quality gate, and are labelled in results. *Evidence:* TuneD's CVEs show the privilege risk [S-01]; MLPerf ties accuracy targets to the reference [S-37].
2. **Yield to the administrator, log every change** (bpftune pattern [S-04]). Before every change, detect whether a knob was set manually. Write an append-only journal entry: knob, old value, new value, reason, evidence ID, rollback handle.
3. **Two-tier adoption pipeline.**
   - Tier R: apply, evaluate, keep or revert, within seconds to minutes.
   - Tier I: build or stage a new image, then boot with `+3` boot counting [S-39] and greenboot-rs `required.d` checks. The checks cover GPU visible, driver loaded, a smoke inference within its latency budget, and a KLD gate [S-40]. The image is blessed only after `boot-complete.target`; otherwise `bootc rollback` [S-43].
   - sched_ext schedulers are Tier R, relying on the kernel's automatic fallback [S-44].
4. **Optimizer core.**
   - Use Optuna (light, embeddable) or Ax/BoTorch (stronger noisy multi-objective BO, qNEHVI) with outcome constraints. Objectives: goodput, J/token, p99 latency. Constraints: KLD ≤ threshold, zero crashes or OOMs, temperatures under limit [S-22][S-24].
   - Seed the search with known-good arms: stock settings, TuneD `throughput-performance` and `latency-performance` [S-02], and engine defaults.
   - Treat crashing configurations as infeasible, not as zero-valued (SLO-Guard) [S-15].
5. **Noise-aware evaluation protocol.**
   - Every comparison runs baseline (A) and candidate (B) as RMIT blocks (ABBA or randomized order) [S-50].
   - The noise floor comes from periodic A/A runs [S-50].
   - Candidates whose within-run CoV exceeds the calibrated floor are rejected as unstable (TUNA) [S-10].
   - **The winner is re-validated** with fresh runs before adoption, to counter the selection bias ("winner's curse") inherent in picking the best of many noisy trials.
   - Adopt only if the lower bound of the effect-size CI (Kalibera & Jones [S-48]; bootstrap or Hodges–Lehmann for non-normal data, per Hoefler & Belli [S-49]) exceeds a minimum effect of interest. *Proposed default: 2 % or 2× the noise floor, whichever is larger; this needs calibration (H-S-03).*
6. **Staged rollout and continuous regression detection.**
   - Adopted configurations enter a time-boxed canary period running real or replayed workloads.
   - Production telemetry and nightly benchmark series feed an E-Divisive change-point detector (Otava) [S-53][S-55].
   - Automatic rollback is triggered by a quality-gate failure, a crash or OOM, a thermal-throttling event, or a change point attributed to the adopted change.
   - Change points need a human or rule-based attribution step, as at MongoDB [S-53].
7. **LLM-assisted proposals are allowed only as a candidate generator** (AutoOS/BYOS style [S-06][S-07]). They are constrained to the knob manifest and always verified by the harness. They never write configuration directly.

**Benchmark harness**

8. **Environment manifest per run.** Record:
   - kernel, firmware/BIOS/AGESA, NVIDIA driver and CUDA versions, engine commit, model hash;
   - governor/EPP, boost state, GPU clocks and power limit, temperatures, fan curves;
   - background services, page-cache state, environment size.

   Randomize the setup factors that are not under test (environment padding, launch order) [S-47][S-52].
9. **Repetition and statistics.**
   - Repeat at several levels: iterations within a process, process restarts, reboots for Tier I changes [S-48].
   - Use a fixed warmup, and report warmup separately.
   - Report the median with a CI, p50/p90/p99, and the effect size with a CI [S-46][S-49].
   - Use dynamic run counts with a relative-standard-deviation stopping rule as in PTS [S-56].
   - For tail metrics, use MLPerf-style minimum durations (600 s) and early-stopping estimates [S-37].
10. **Metric spec pinned in-repo.**
    - Define TTFT, TPOT, ITL, E2E, throughput and goodput explicitly; tools disagree [S-58].
    - Run vLLM `bench serve` / GuideLLM / AIPerf through adapters that record tool versions [S-14][S-58][S-59]; llama-bench for llama.cpp [S-18].
    - Default SLO classes are borrowed from MLPerf's interactive and conversational limits [S-37].
11. **Model load time** is measured cold (after `sync; echo 3 > drop_caches` [S-45]) and warm, broken down into storage read, deserialization, GPU upload, and kernel or autotune warmup.
12. **Quality gates per modality.**
    - LLM: KLD and same-top-p against stored reference logits, with the 16-bit storage floor subtracted [S-62]; plus a periodic lm-eval-harness subset [S-66].
    - Images: fixed seed, LPIPS against the reference, and CLIP score [S-69][S-70]. FID only for release-level checks, because it needs large sample sets [S-68].
    - Audio: WER on a fixed set.
    - Embeddings: an MTEB retrieval subset [S-67].
    - Batched serving needs distributional gates rather than exact match [S-63].
13. **Energy measurement.**
    - An external wall meter (ideally one supported by SPEC PTDaemon) is the ground truth for headline comparisons, with the measurement boundary declared as in MLPerf Power [S-36].
    - NVML energy counters and RAPL are used for attribution within a run, calibrated against the meter [S-29][S-30].
    - Report J/token (prefill and decode separately), J/image and J/request at a fixed SLO.
14. **Ubuntu baseline protocol.**
    - Same hardware, same model files and engine builds. Compare stock Ubuntu LTS, Ubuntu + TuneD profile, and AI Linux.
    - The middle arm separates "tuning anyone can do" from platform-specific gains.
    - Interleave A/B boots with randomized order (RMIT) [S-50].
15. **Proxmox i5-11500 node** serves as the controller, result store and change-point detector, and as a CPU-only benchmark target. It is not used for GPU numbers. VM noise must be calibrated with A/A runs before its results are trusted [S-50][S-74].

## Optimization opportunities

| ID | Level | Opportunity | Expected benefit | Feasibility | Risk | Effort | Evidence | Novelty |
|---|---|---|---|---|---|---|---|---|
| O-S-01 | platform | Harness with RMIT, A/A noise calibration, effect-size CIs and winner re-validation | H: prevents false adoptions, which the literature shows are common | High (standard statistics) | Low | M | [S-10][S-47][S-48][S-50] | known-elsewhere |
| O-S-02 | userspace | GPU power-limit/clock tuning toward J/token at a fixed SLO | H for energy if decode is memory-bound; throughput roughly flat (unproven on the 4090) | High (NVML, reversible) | Low | S | [S-26][S-71][S-72] | established |
| O-S-03 | runtime | Wrap the vLLM auto_tune/bench sweep and llama-bench sweeps into the optimizer with goodput objectives | H: serving parameters dominate throughput and latency | High | Low (crash handling needed) | S | [S-13][S-14][S-15][S-18] | established |
| O-S-04 | platform | Constrained multi-objective BO over Tier-R knobs (Optuna/Ax) | M: finds combinations; gains may be small for GPU-bound workloads | High | Medium (noise) | M | [S-22][S-24][S-10] | known-elsewhere |
| O-S-05 | userspace | Tier-I changes (kernel command line, kernel or driver version) via bootc + boot counting + greenboot-rs | M: enables image-level tuning with automatic rollback | High (upstream pieces) | Low–Medium | M | [S-39][S-40][S-43] | established |
| O-S-06 | platform | Continuous E-Divisive change-point detection (Otava) on benchmark and telemetry series | M (safety): catches slow drift and regressions after updates | High | Low (needs attribution) | S | [S-53][S-54][S-55] | established |
| O-S-07 | runtime | KLD/same-top-p quality-gate service with a reference-logit store | H (safety): enforces "no silent quality reduction" | High for llama.cpp; Medium for other engines | Low | M | [S-62][S-37] | novel as an OS-level gate |
| O-S-08 | kernel | Per-workload sched_ext scheduler selection, attached and detached by the optimizer | L–M: mainly tail latency under mixed load | Medium | Low (kernel fallback) | M | [S-44] | known-elsewhere |
| O-S-09 | runtime | Persist and pre-warm autotune/JIT caches (Triton, CUDA graphs) across boots | M: shorter cold start, more stable first-request latency | Medium | Low | M | [S-19][S-21] | known-elsewhere |
| O-S-10 | userspace | Cold vs warm model-load benchmark and tuning (readahead, mmap vs direct read) | M: load time is a user-visible metric | High | Low | S | [S-45][S-18] | known-elsewhere |
| O-S-11 | runtime | AIConfigurator-style per-operation performance database for consumer GPUs (RTX 4090) to prune the search | M: cuts the number of trials | Low–Medium (no consumer data) | Medium (prediction error) | L | [S-16][S-17] | novel (consumer) |
| O-S-12 | platform | Energy stack: wall meter plus calibrated NVML/RAPL attribution; J/token reporting | M: credible energy claims | Medium (hardware cost) | Low | M | [S-29][S-30][S-33][S-36] | known-elsewhere |
| O-S-13 | platform | Allow-listed LLM-assisted knob proposals (AutoOS/BYOS style), verified by the harness | L–M: wider search with fewer expert hours | Medium | Medium (hallucinated knobs; mitigated by the allow-list) | L | [S-06][S-07][S-09] | known-elsewhere |
| O-S-14 | application | Concurrency and interference benchmark (LLM + image + embeddings + desktop) with goodput and tail metrics | M: reflects real local-AI usage | Medium | Low | M | [S-57][S-37] | novel for desktops |

## Hypotheses

**Hypothesis H-S-01** (GPU power cap on decode)
- *Statement:* On the RTX 4090, single-stream llama.cpp decode of models of 7B parameters or more keeps at least 95 % of tokens/s at a power limit of about 70 % of default, and improves wall-measured J/token by at least 15 %.
- *Rationale:* decode is memory-bandwidth-bound; anecdotal RTX 3090/4090 reports [S-71][S-72]; Zeus trade-off curves [S-26].
- *Test to falsify:* sweep the power limit from 50 % to 100 % in 5 % steps with RMIT ordering, a wall meter and NVML. Prefill and image generation are measured separately.
- *Risk:* compute-bound phases (prefill, diffusion) lose throughput; the evidence base is anecdotal.

**Hypothesis H-S-02** (OS-level gains for GPU-bound inference are small)
- *Statement:* For GPU-resident LLM decode, AI Linux runtime knobs improve throughput over Ubuntu + TuneD by less than 2 %, within noise. Measurable gains concentrate in model load time, CPU offload / MoE hybrid execution, and tail latency under concurrency.
- *Rationale:* the GPU dominates the critical path; AutoOS-style gains were measured on CPU-centric UnixBench [S-75].
- *Test:* the Ubuntu baseline protocol (design implication 14) across workload classes.
- *Risk:* misdirected optimization effort if the hypothesis is wrong in either direction.

**Hypothesis H-S-03** (false adoption without re-validation)
- *Statement:* A BO optimizer over Tier-R knobs without re-validation adopts a "winner" whose gain disappears on fresh re-runs in at least 30 % of campaigns. With RMIT and re-validation, this falls below 5 %.
- *Rationale:* noise and instability in tuning [S-10]; measurement bias [S-47]; A/A artefacts [S-50].
- *Test:* run 20 campaigns per arm, then hold-out re-measurement of every adopted winner.
- *Risk:* the thresholds are guesses; the cost of re-validation may make the optimizer too slow.

**Hypothesis H-S-04** (KLD beats task accuracy as a gate)
- *Statement:* Mean KLD plus same-top-p detects numerics-changing knobs (KV-cache dtype, quantization level, math mode) with fewer evaluated tokens and higher sensitivity than lm-eval-harness task accuracy.
- *Rationale:* KLD measures distribution drift directly, while task scores are coarse [S-62][S-66].
- *Test:* inject graded degradations and compare detection power and wall time for each gate.
- *Risk:* KLD may flag harmless drift; thresholds that correlate with user-visible quality are unknown.

**Hypothesis H-S-05** (batched serving defeats exact-match gates)
- *Statement:* With default vLLM kernels on the RTX 4090, 1000 identical temperature-0 requests under varying concurrency produce more than one unique completion. Exact-match quality gates therefore produce false positives.
- *Rationale:* batch-size-dependent reduction order [S-63][S-64].
- *Test:* replicate the experiment at concurrency 1, 8 and 64 and count unique completions; repeat with batch-invariant mode, if available, and measure its cost.
- *Risk:* low; it decides the gate design.

**Hypothesis H-S-06** (cold load is not limited by storage bandwidth)
- *Statement:* Cold-cache load time for a large safetensors or GGUF model on PCIe 5.0 NVMe is dominated by the read path (page-cache population, readahead, deserialization, GPU upload) rather than raw device bandwidth. Read-path tuning changes cold load time by more than 20 %.
- *Rationale:* `drop_caches` semantics [S-45]; speculative, as no direct evidence was found.
- *Test:* measure cold and warm loads with stage-level timestamps across mmap, buffered and direct-read loaders.
- *Risk:* the gain may be negligible if loaders already saturate the device.

**Hypothesis H-S-07** (NVML partial sampling on Ada)
- *Statement:* NVML instantaneous power on the RTX 4090 (Ada) also samples only part of runtime, as on A100/H100. For workloads under 1 s, NVML-derived energy differs from inline-measured GPU energy by more than 5 %.
- *Rationale:* [S-29] tested datacenter parts only.
- *Test:* run square-wave kernels of varying period and compare NVML power, the NVML energy counter, and an inline 12V-2x6/PCIe-slot power measurement.
- *Risk:* consumer-board telemetry may differ; inline measurement hardware is needed.

**Hypothesis H-S-08** (change-point detection detects 5 % steps quickly)
- *Statement:* E-Divisive (Otava) on nightly series with harness-level noise (CoV ≤ 2 %) detects injected 5 % step regressions within 5 subsequent points, at no more than one false alarm per metric per quarter.
- *Rationale:* used in practice in CI [S-53][S-54].
- *Test:* replay synthetic step injections on recorded series.
- *Risk:* higher noise forces larger windows and slower detection.

**Hypothesis H-S-09** (sched_ext helps tails, not throughput)
- *Statement:* A latency-oriented sched_ext scheduler improves interactive and desktop p99 latency while background inference runs, but does not change GPU-bound inference throughput by more than 1 %.
- *Rationale:* GPU-bound workloads spend little time in CPU scheduling decisions; sched_ext can be swapped safely [S-44].
- *Test:* concurrency benchmark (O-S-14) with and without the scheduler, in RMIT order.
- *Risk:* CPU-offload and MoE workloads may behave differently.

## Contradictory or weak evidence

- **Cost of batch-invariant kernels:** secondary summaries report about 20 % slowdown (course slides) and about 2× (a blog summary). LLM-42 measured a 194 vs 527 TFLOPS matmul peak, a 63 % slowdown [S-64][S-65]. The primary post could not be fetched [S-63].
- **CodeCarbon accuracy:** 80–96 % of wattmeter readings in a 2023 review [S-34], against errors up to 40 % in a 2025 validation [S-33]. The difference likely comes from measurement boundaries (per-process vs whole system) and hardware.
- **TUNA figures:** the paper's 39.0 % unstable configurations [S-10] differ from a third-party summary's "63.3 % of best configurations degrade by ≥30 %". These may be different quantities. Only the paper figure is used here.
- **Optuna constrained multi-objective search:** the v4.5 docstring says `constraints_func` is unsupported for multi-objective problems, while the v4.5 release blog shows it working. v5.0 then deprecates `constraints_func` [S-22].
- **AutoOS 25 % and Wayfinder 24 %** were measured on general or application benchmarks against default configurations [S-06][S-09][S-75]. They do not transfer to AI workloads.
- **Wayfinder vs BO:** neural-network search beat BO on Unikraft [S-09], while the BO literature (qNEHVI) is strong in noisy multi-objective settings [S-24]. No head-to-head comparison exists on our problem class.
- **AIConfigurator's gains** are self-reported and validated only on datacenter GPUs [S-16].
- **vLLM auto_tune** uses `gpu-memory-utilization` 1.0 on CUDA when its search is skipped [S-13]. Results may not transfer to deployments at the usual 0.9 default (the 0.9 figure was seen only in third-party guidance, not verified upstream).
- **Power-capping evidence for consumer GPUs is grade D** [S-71][S-72].
- **RAPL on AMD:** the only accuracy validation found is for Zen 2 [S-30]. Zen 4 (7950X3D) is unvalidated.

## Open questions

1. Which Tier-R knobs matter at all for single-GPU local inference? A screening design (Plackett–Burman or Morris) should come before BO.
2. What KLD and same-top-p thresholds correlate with user-visible quality for each model family and quantization? Are they stable across engines?
3. Which affordable external power meter (ideally supported by SPEC PTDaemon) and which inline GPU power measurement can the project standardize on?
4. How should the harness correct for slow drift: ambient temperature, thermal-paste aging, firmware updates? Should it use reference "canary" runs on every benchmark day?
5. How should competing objectives be weighted for concurrent workloads (LLM + image generation + desktop)? Fixed SLO classes, or user-declared priorities?
6. On the 7950X3D, do CCD affinity choices (V-Cache CCD vs frequency CCD) need to be a first-class knob for CPU-side inference and offload? (Cross-reference 02/03.)
7. MLPerf Training rules (time-to-train to a quality target) and a fine-tuning benchmark methodology were not reviewed. A follow-up is needed for training and fine-tuning workloads.
8. How do soak tests, OOM-policy validation (systemd-oomd, earlyoom, GPU OOM) and stress tools fit into the canary stage, and how long must a canary run to bound the risk of rare failures?
9. Is the Proxmox host's virtualization noise low enough for CPU-only benchmarks, or should it serve only as the controller?

## Access limitations

- **WebFetch DNS failures (not retried):**
  - `systemd.io`: boot assessment was read from the GitHub copy instead [S-39].
  - `thinkingmachines.ai`: the nondeterminism post was seen only via secondary summaries [S-63][S-64].
  - `docs.vllm.ai`: sweep docs were seen only via search snippets [S-14].
  - `arxiv.org`: arXiv content (TUNA, Perseus, part-time power, AIConfigurator, MLPerf Power and others) was seen only through search-result summaries.
  - `docs.kernel.org` was reported as failing by the lead and was not attempted. Kernel docs came from the GitHub mirror [S-45] or search snippets [S-44].
- **Cited from bibliographic knowledge without re-fetching in this session** (verify in Phase 1): [S-12], [S-19], [S-20], [S-21], [S-25], [S-26], [S-46], [S-47], [S-52], [S-66], [S-67], [S-68], [S-69], [S-70], [S-76], [S-77].
- **Not researched (budget):** SGLang `bench_serving`, Scaphandre status, MLPerf Training rules, stress tools (stress-ng, gpu-burn), Hoefler & Belli's full rule text (only the abstract and slides were seen).

## Sources

| ID | Title | URL | Date | Accessed | Grade | Notes |
|---|---|---|---|---|---|---|
| S-01 | TuneD GitHub releases (v2.24.1–v2.28.0) | https://github.com/redhat-performance/tuned/releases | 2026-08-22 (year inferred) | 2026-11-26 | B | Release notes; CVE fixes in 2.24.1 |
| S-02 | RHEL 10: Optimizing system performance with TuneD | https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/monitoring_and_managing_system_status_and_performance/optimizing-system-performance-with-tuned | n.d. | 2026-11-26 | A | Profile semantics (search summary plus Oracle Linux docs) |
| S-03 | Phoronix: Red Hat releases TuneD 2.26 | https://phoronix.com/news/Red-Hat-TuneD-2.26 | 2025-08-25 | 2026-11-26 | C | Release date |
| S-04 | oracle/bpftune README | https://github.com/oracle/bpftune | n.d. | 2026-11-26 | B | Design principles, tuner list |
| S-05 | Phoronix: Oracle releases updated bpftune 0.4-1 | https://phoronix.com/news/Oracle-bpftune-0.4-1 | 2025-12 | 2026-11-26 | C | Via search summary |
| S-06 | Chen et al., AutoOS (ICML 2024, PMLR 235) | https://proceedings.mlr.press/v235/chen24at.html | 2024-07 | 2026-11-26 | A | Up to 25 % vs vendor config (claim) |
| S-07 | BYOS: knowledge-driven LLMs for OS kernel tuning | https://arxiv.org/abs/2503.09663 | 2025-03 | 2026-11-26 | B | Kconfig knowledge graph |
| S-08 | Wayfinder: Towards Automatically Deriving Optimal OS Configurations (APSys '21) | https://eprints.lancs.ac.uk/id/eprint/157674 | 2021 | 2026-11-26 | A | Platform paper |
| S-09 | Wayfinder: Automated Operating System Specialization | https://arxiv.org/abs/2603.23425 | 2026-03 | 2026-11-26 | B | Preprint; EuroSys '26 per search summary |
| S-10 | Kroth et al., TUNA: Tuning Unstable and Noisy Cloud Applications (EuroSys '25) | https://arxiv.org/abs/2503.01801 | 2025-03 | 2026-11-26 | A | Via search summary; code at uw-mad-dash/TUNA |
| S-11 | DarwinGame: tournaments for tuning in noisy clouds | https://arxiv.org/abs/2509.25090 | 2025-09 | 2026-11-26 | B | Venue (ASPLOS '25) per MIT DSpace, unverified |
| S-12 | Hao et al., LinnOS (OSDI '20) | https://www.usenix.org/conference/osdi20/presentation/hao | 2020-11 | not fetched | A | Bibliographic knowledge |
| S-13 | vLLM benchmarks/auto_tune README | https://github.com/vllm-project/vllm/blob/main/benchmarks/auto_tune/README.md | n.d. | 2026-11-26 | B | Fetched |
| S-14 | vLLM docs: Benchmark sweeps | https://docs.vllm.ai/en/v0.14.0/benchmarking/sweeps/ | n.d. | 2026-11-26 | A | Search snippet only (host blocked) |
| S-15 | SLO-Guard: crash-aware autotuning for LLM serving | https://arxiv.org/abs/2604.17627 | 2026-04 | 2026-11-26 | B | Search snippet |
| S-16 | AIConfigurator: configuration optimization for multi-framework LLM serving | https://arxiv.org/abs/2601.06288 | 2026-01 | 2026-11-26 | B | Self-reported gains |
| S-17 | NVIDIA blog: Removing the guesswork from disaggregated serving | https://developer.nvidia.com/blog/removing-the-guesswork-from-disaggregated-serving | n.d. | 2026-11-26 | B | Via search summary |
| S-18 | llama.cpp llama-bench README | https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md | n.d. | 2026-11-26 | B | Fetched |
| S-19 | Triton `triton.autotune` API docs | https://triton-lang.org/main/python-api/generated/triton.autotune.html | n.d. | not fetched | A | Bibliographic knowledge |
| S-20 | Zheng et al., Ansor (OSDI '20) | https://www.usenix.org/conference/osdi20/presentation/zheng | 2020-11 | not fetched | A | Bibliographic knowledge |
| S-21 | Shao et al., Tensor Program Optimization with Probabilistic Programs (MetaSchedule, NeurIPS '22) | https://arxiv.org/abs/2205.13603 | 2022 | not fetched | A | Bibliographic knowledge |
| S-22 | Optuna release notes v4.4/v4.5/v5.0.0 | https://newreleases.io/project/pypi/optuna/release/5.0.0 | 2025–2026 | 2026-11-26 | B | Plus Optuna v4.5 blog; dates not verified |
| S-23 | InfoQ: Meta's Ax 1.0 | https://infoq.com/news/2025/12/ax-hyperparameter-optimization/ | 2025-12 | 2026-11-26 | C | Stability announcement |
| S-24 | Daulton et al., Parallel Bayesian optimization of multiple noisy objectives with EHVI (qNEHVI, NeurIPS '21) | https://arxiv.org/abs/2105.08195 | 2021 | 2026-11-26 | A | Via search summary |
| S-25 | Lindauer et al., SMAC3 (JMLR 2022) | https://jmlr.org/papers/v23/21-0888.html | 2022 | not fetched | A | Bibliographic knowledge |
| S-26 | You et al., Zeus (NSDI '23) | https://www.usenix.org/conference/nsdi23/presentation/you | 2023-04 | not fetched | A | Characterization also confirmed in [S-28] |
| S-27 | ml-energy/zeus README | https://github.com/ml-energy/zeus | 2026-02 (latest news item) | 2026-11-26 | B | Fetched |
| S-28 | Chung et al., Perseus: Reducing energy bloat in large model training (SOSP '24) | https://arxiv.org/abs/2312.06902 | 2024-11 | 2026-11-26 | A | Up to 30 % energy reduction (claim) |
| S-29 | Yang, Adámek, Armour: Part-time power measurements: nvidia-smi's lack of attention | https://arxiv.org/abs/2312.02741 | 2024-12 (v3) | 2026-11-26 | B | Via search summary; A100/H100 |
| S-30 | Schöne et al., Energy efficiency aspects of the AMD Zen 2 architecture | https://arxiv.org/abs/2108.00808 | 2021-08 | 2026-11-26 | B | RAPL vs external measurement |
| S-31 | Hackenberg et al., Power measurement techniques on standard compute nodes (ISPASS 2013) | https://web.eece.maine.edu/~vweaver/classes/ece571_2014f/papers/ispass2013-power_measurement.pdf | 2013 | 2026-11-26 | A | Via search summary |
| S-32 | Raffin & Trystram, Dissecting the software-based measurement of CPU energy (RAPL) | https://arxiv.org/abs/2401.15985 | 2024-01 | 2026-11-26 | B | Via search summary |
| S-33 | Ground-truthing AI energy consumption: validating CodeCarbon | https://arxiv.org/abs/2509.22092 | 2025-09 | 2026-11-26 | B | Up to 40 % error (claim) |
| S-34 | How to estimate carbon footprint when training deep learning models? | https://arxiv.org/abs/2306.08323 | 2023-06 | 2026-11-26 | B | Tool vs wattmeter table |
| S-35 | CNCF blog: Kepler re-architected | https://cncf.io/blog/2026/06/30/kepler-re-architected-improved-power-accuracy-and-a-community-call-to-action | 2026-06-30 | 2026-11-26 | B | Via search summary |
| S-36 | Tschand et al., MLPerf Power (HPCA 2025) | https://arxiv.org/abs/2410.12032 | 2025-02 (v2) | 2026-11-26 | A | Analyzer details via a third-party summary |
| S-37 | MLPerf Inference rules (inference_policies) | https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc | n.d. (refers to v6.1 round) | 2026-11-26 | A | Fetched |
| S-38 | MLCommons MLPerf Client page; v1.5 press release | https://mlcommons.org/benchmarks/client | 2025-11-18 | 2026-11-26 | B | Linux CLI experimental |
| S-39 | systemd docs: Automatic Boot Assessment | https://github.com/systemd/systemd/blob/main/docs/AUTOMATIC_BOOT_ASSESSMENT.md | n.d. | 2026-11-26 | A | systemd.io blocked; GitHub copy |
| S-40 | Fedora 43 change proposal: Greenboot Rust rewrite | https://lists.fedoraproject.org/archives/list/devel-announce@lists.fedoraproject.org/thread/27UM3UPGGRHQZ5756WF5JBI7HSDMVFN2/ | 2025 | 2026-11-26 | B | Via search summary |
| S-41 | Fedora Packages: greenboot-rs | https://packages.fedoraproject.org/pkgs/greenboot-rs/greenboot | n.d. | 2026-11-26 | B | 0.16.x versions |
| S-42 | Red Hat Developer: greenboot automates rollbacks | https://developers.redhat.com/articles/2024/08/12/greenboot-automate-rollbacks-atomically-updated-systems | 2024-08-12 | 2026-11-26 | B | required.d / wanted.d |
| S-43 | bootc-rollback(8) man page (bootc 1.8.0); RHEL 10 image-mode docs | https://mankier.com/8/bootc-rollback | n.d. | 2026-11-26 | A | Via search summary |
| S-44 | Linux kernel docs: Extensible Scheduler Class (sched_ext) | https://docs.kernel.org/scheduler/sched-ext.html | n.d. (7.x docs) | 2026-11-26 | A | Search snippets; host blocked |
| S-45 | Linux Documentation/admin-guide/sysctl/vm.rst (drop_caches) | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/sysctl/vm.rst | n.d. | 2026-11-26 | A | Fetched |
| S-46 | Georges, Buytaert, Eeckhout: Statistically rigorous Java performance evaluation (OOPSLA '07) | https://doi.org/10.1145/1297027.1297033 | 2007 | not fetched | A | Bibliographic knowledge |
| S-47 | Mytkowicz et al.: Producing wrong data without doing anything obviously wrong! (ASPLOS '09) | https://doi.org/10.1145/1508244.1508275 | 2009 | not fetched | A | Bibliographic knowledge |
| S-48 | Kalibera & Jones: Rigorous benchmarking in reasonable time (ISMM '13) | https://kar.kent.ac.uk/id/document/3127886 | 2013 | 2026-11-26 | A | Via search summary |
| S-49 | Hoefler & Belli: Scientific benchmarking of parallel computing systems (SC '15) | https://doi.org/10.1145/2807591.2807644 | 2015-11 | 2026-11-26 | A | Abstract and author slides (C) only |
| S-50 | Abedi & Brecht: Conducting repeatable experiments in highly variable cloud environments (ICPE '17) | https://doi.org/10.1145/3030207.3030229 | 2017 | 2026-11-26 | A | RMIT; via search summary |
| S-51 | Bulej et al.: Duet benchmarking (ICPE '20) | https://arxiv.org/abs/2001.05811 | 2020-01 | 2026-11-26 | A | Search result only |
| S-52 | Curtsinger & Berger: STABILIZER (ASPLOS '13) | https://doi.org/10.1145/2451116.2451141 | 2013 | not fetched | A | Bibliographic knowledge |
| S-53 | Daly et al.: Change point detection in software performance testing (ICPE '20) | https://arxiv.org/abs/2003.00584 | 2020-03 | 2026-11-26 | A | E-Divisive at MongoDB |
| S-54 | Fleming et al.: Hunter: using change point detection to hunt for performance regressions | https://arxiv.org/abs/2301.03034 | 2023-01 | 2026-11-26 | B | Via search summary |
| S-55 | Apache Otava (incubating) status page; Ingo: 8 years of optimizing Apache Otava | https://incubator.apache.org/clutch/otava.html | 2025-05 (paper) | 2026-11-26 | B | Renamed from Hunter 2025-02-12 |
| S-56 | Phoronix: PTS 8.8 M2 and 10.2 M3 release notes | https://www.phoronix.com/news/Phoronix-Test-Suite-8.8-M2 | n.d. | 2026-11-26 | C | Dynamic run-count thresholds |
| S-57 | Zhong et al.: DistServe (OSDI '24) | https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin | 2024-07 | 2026-11-26 | A | Goodput definition; claims |
| S-58 | NVIDIA AIPerf docs (GenAI-Perf vs AIPerf matrix); NVIDIA blogs on AIPerf and LLM benchmarking concepts | https://docs.nvidia.com/aiperf/getting-started/gen-ai-perf-vs-ai-perf-cli-feature-comparison-matrix | n.d. | 2026-11-26 | B | Via search summary |
| S-59 | vllm-project/guidellm | https://github.com/vllm-project/guidellm | n.d. | 2026-11-26 | B | Via search summary (README/PyPI) |
| S-60 | ray-project/llmperf (archived) | https://github.com/ray-project/llmperf | archived 2025-12-17 | 2026-11-26 | B | Fetched |
| S-61 | huggingface/inference-benchmarker | https://github.com/huggingface/inference-benchmarker | n.d. | 2026-11-26 | B | Via search summary |
| S-62 | llama.cpp perplexity / KL-divergence README | https://github.com/ggml-org/llama.cpp/blob/master/tools/perplexity/README.md | n.d. | 2026-11-26 | B | Fetched |
| S-63 | He / Thinking Machines Lab: Defeating nondeterminism in LLM inference | https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/ | 2025-09 | blocked | B | Not fetched (DNS); content via [S-64] |
| S-64 | Simon Willison: link-blog summary of [S-63] | https://simonwillison.net/2025/Sep/11/defeating-nondeterminism | 2025-09-11 | 2026-11-26 | C | Secondary summary |
| S-65 | LLM-42: enabling determinism in LLM inference with verified speculation | https://arxiv.org/abs/2601.17768 | 2026-01 | 2026-11-26 | B | 194 vs 527 TFLOPS (claim) |
| S-66 | EleutherAI lm-evaluation-harness | https://github.com/EleutherAI/lm-evaluation-harness | n.d. | not fetched | B | Bibliographic knowledge |
| S-67 | Muennighoff et al.: MTEB (EACL 2023) | https://arxiv.org/abs/2210.07316 | 2022-10 | not fetched | A | Bibliographic knowledge |
| S-68 | Heusel et al.: GANs trained by a two time-scale update rule (FID) | https://arxiv.org/abs/1706.08500 | 2017 | not fetched | A | Bibliographic knowledge |
| S-69 | Hessel et al.: CLIPScore (EMNLP 2021) | https://arxiv.org/abs/2104.08718 | 2021 | not fetched | A | Bibliographic knowledge |
| S-70 | Zhang et al.: LPIPS (CVPR 2018) | https://arxiv.org/abs/1801.03924 | 2018 | not fetched | A | Bibliographic knowledge |
| S-71 | Blog: RTX 3090 power-limit sweet spot | https://jeanfbrito.github.io/posts/rtx-3090-power-limit-sweet-spot/ | n.d. | 2026-11-26 | D | Single-user report |
| S-72 | dev.to: LLM fine-tuning on RTX 4090, 90 % performance at 55 % power | https://dev.to/maximsaplin/llm-fine-tunig-on-rtx-4090-90-performance-at-55-power-29o3 | n.d. | 2026-11-26 | D | Single-user report |
| S-73 | TokenPowerBench: benchmarking the power consumption of LLM inference | https://arxiv.org/abs/2512.03024 | 2025-12 | 2026-11-26 | B | Search result only |
| S-74 | Performance Roulette: how cloud weather affects ML-based system optimization (NeurIPS '23 workshop) | https://neurips.cc/virtual/2023/84459 | 2023-12 | 2026-11-26 | B | Search result only |
| S-75 | Survey: Integrating AI into operating systems | https://arxiv.org/abs/2407.14567 | 2024-07 | 2026-11-26 | B | Notes the AutoOS metric (UnixBench) |
| S-76 | NixOS manual (generations, rollback) | https://nixos.org/manual/nixos/stable/ | n.d. | not fetched | A | Bibliographic knowledge |
| S-77 | openSUSE snapper | https://github.com/openSUSE/snapper | n.d. | not fetched | B | Bibliographic knowledge |
