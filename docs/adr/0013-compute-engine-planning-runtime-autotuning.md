# ADR-0013: AI Compute Engine — capability-driven planning, engine-native fitting, managed runtime, autotuning on the campaign stack

> Status: Proposed · Date: 2026-11-27 · Deciders: maintainers (pending)

## Context

The roadmap's MVP (Phase 2) needs a placement planner, runtime-parameter tuning and a way to run
several AI workloads on one consumer GPU without out-of-memory crashes
(`docs/architecture/hardware-abstraction.md`, `self-optimization.md`, overview "workload classes").

Three facts shape the design:

- **llama.cpp fits layers itself.** At the pinned commit 98cc730, llama.cpp already fits layers to
  free device memory at load time (`--fit on`, 1024 MiB margin, `-ngl auto`; engine help text). It
  does this per process only and knows nothing about other workloads.
- **Statistical machinery exists.** Pre-registered campaigns, RMIT scheduling, BH-corrected
  comparisons, A/A calibration, a content-addressed store and a KLD quality gate are already
  implemented (Phase 1.1) and must be reused (ADR-0004 §6).
- **Nothing is hardware-validated.** ADR-0010 requires every tuning default to pass the quality and
  stability gates on hardware before it is merged.

## Options considered

1. **Own placement engine that overrides the inference engine** (always pass explicit `-ngl` and
   similar flags).
   - Pro: full control.
   - Con: duplicates and fights llama.cpp's fit; our estimates are uncalibrated, so this would make
     the default *worse* until calibrated.
2. **Delegate everything to the engine.**
   - Pro: simplest.
   - Con: no cross-process admission control; no prediction for multi-model use; no tuning loop.
3. **Predict, admit, let the engine fit, tune on top.**
   - Planner: our prediction feeds admission control and candidate generation.
   - Runtime: the default start uses the engine defaults (fit) and passes only the context size.
   - Tuning: candidate configurations are measured against those engine defaults with the existing
     campaign stack, and only a confirmed, lossless (or quality-gated) delta is adopted.

## Decision (proposed)

Option 3:

- **Capability engine.**
  - Reads CPU topology per LLC domain (CCD roles), ISA level, NUMA, memory and storage.
  - Reads accelerators via providers (NVIDIA: NVML → nvidia-smi → sysfs; AMD, Intel and NPU via
    sysfs).
  - A backend counts as *usable* only when three things are present: a device, its driver stack and
    an installed engine build.
  - Unknown values stay null and are listed in `unavailable`. Bandwidth carries provenance
    (`datasheet:<source>`, or `theoretical` with the formula).
- **Model analysis.**
  - A header-only, bounded GGUF reader (no tensor data, no mmap, limits on all counts); safetensors
    headers are read the same way.
  - Weights, experts and KV per layer are computed exactly from the file. Buffers are heuristic and
    labeled *uncalibrated* (H-R-11).
- **Planner.**
  - Memory fit per device for full GPU, MoE expert split or layer split, otherwise CPU.
  - Maximum context, engine/backend choice and actions (e.g. which build to install).
  - Theoretical decode bound only when every used bandwidth is known.
  - Lossy KV types only with explicit opt-in.
- **Runtime.**
  - Unprivileged process manager for llama-server.
  - Admission control: the sum of predictions of running instances plus the new one must stay
    within the budget, and the new one must fit memory free now.
  - Priority classes via nice and `oom_score_adj`, which are only raised.
  - Preemption of lower classes only on request.
  - CPU placement via per-process affinity, default `none`. Placement policies are **experimental**
    (H-H-01, H-K-09, H-K-11).
  - Process identity is re-verified before any signal.
  - Settings from tuning records are validated, and lossy ones require `--allow-lossy` at each start.
- **Autotuning.**
  - Baseline = engine defaults.
  - Screening campaign → winner → confirmatory campaign with fresh randomization → adoption only on a
    significant improvement without guard regressions. Lossy candidates also need a passed KLD gate.
  - Failed runs count as infeasible.
  - Records are keyed by (hardware profile, engine build, model, objective) and invalidated by any
    change of these. `verify` reverts an adoption whose advantage disappeared.
  - `calibrate` provides the A/A noise floor on exactly the tuning workload.
- **No host knobs.** The compute engine changes no system setting; host knobs stay in the
  transactional system layer (ADR-0011).

## Consequences

- Positive:
  - The default behaviour equals stock llama.cpp, so the compute engine cannot make an
    un-tuned start slower than the engine itself.
  - Every adopted change is a measured, evidenced delta against that default.
  - Multi-model use gets an explicit memory budget instead of a CUDA OOM.
- Negative / costs:
  - Admission uses uncalibrated estimates (conservative so far: 5–7× the RSS of a 17 MB test model
    on the cloud VM, a status reading without store ID; no large-model data yet).
  - Tuning costs measurement time, and screening plus confirmation per model takes minutes to hours.
- Risks and mitigations:
  - Estimates can also *under*-predict for unmodeled architectures. Mitigations: the margin
    (`--fit-target` default), and llama.cpp's own fit still runs at load.
  - Experimental placement could hurt (H-H-01 predicts single-CCD decode limits). It is never a
    default and never adopted without confirmation.

## Validation / revisit triggers

- **VL-1.5-01 … 05** (`tools/hw_validate.py` on the reference workstation):
  - capability expectations;
  - prediction vs measured RSS/VRAM ratios (H-R-11);
  - managed CUDA server;
  - admission refusal on 24 GB;
  - calibrated tuning decisions (H-R-12).
- **Revisit triggers:**
  - prediction error > ±25 % on the reference models (calibrate the overheads or switch to the
    engine's own fit report);
  - llama.cpp removes or changes `--fit`;
  - a second engine (vLLM) needs the managed runtime;
  - Phase 2 continuous tuning needs a daemon (ADR-0011 option 2).

## References

- `docs/architecture/compute-engine.md`
- `docs/architecture/hardware-abstraction.md`
- `docs/architecture/self-optimization.md`
- ADR-0004, ADR-0008, ADR-0010, ADR-0011
