# CURRENT — hot context

> Last updated: 2026-11-26 · Keep ≤ 100 lines · Detail lives in linked docs (ADR-0002)

## Where we are

- **Phase 0 (Research & Architecture): deliverables complete, awaiting
  maintainer review.** No code, no image, no measurements exist.
- Research: 6 evidence-graded documents, 486 sources (A 186 / B 193 / C 85 /
  D 22), 79 opportunities → 30 prioritized items, 53 hypotheses.
  Access limits during research: `docs/research/00-method.md`.

## Key findings (one line each; evidence in `docs/research/`)

1. **The gap is the combination**: no project couples OS/runtime tuning,
   per-machine benchmarking, quality gates and full-stack rollback (01 §F6).
2. **Where OS work plausibly matters**: CPU/MoE offload on asymmetric
   multi-CCD CPUs, model loading, tail latency under contention, energy —
   *not* GPU-resident decode (H-L-01, H-S-02, H-D-03 — untested).
3. **New kernel default to watch**: `SCHED_CACHE` (since 7.2) packs a process's
   threads into one LLC; untested on AI workloads (H-K-01). Ubuntu 26.04
   (Linux 7.0) does not have it yet.
4. **sched_ext is safe as opt-in** (runtime attach, automatic fallback,
   cgroup sub-schedulers since 7.3) (02).
5. **RTX 4090 facts**: FP8 yes, no native FP4, FA2-family only, P2P
   unsupported, GDS compat-mode only on GeForce (03, 04).
6. **Every quantization is lossy; accuracy hides it** → KLD/flip-rate gates;
   engine bugs can destroy quality silently (04).
7. **Noise is the main failure mode of auto-tuning** → RMIT, A/A calibration,
   winner re-validation (06).
8. **Distro choice is a strategy choice** (layer vs own image), not a quality
   ranking; Ubuntu 26.04 best as host, Fedora bootc / Debian+mkosi as image
   candidates (05).

## Decisions

| ADR | Decision | Status |
|-----|----------|--------|
| 0001–0004 | ADRs; AGENTS.md canonical + tiered context; Phase 0 + evidence policy; guardrails | Accepted |
| 0005 | Layer-first delivery; image base chosen by bake-off | **Proposed** |
| 0006 | Ubuntu 26.04 LTS stock as benchmark baseline; arms U0/U1/U2/A | **Proposed** |
| 0007 | Abstract management, not compute; provider plugins | **Proposed** |
| 0008 | Self-optimizer safety tiers T0–T3 and adoption rules | **Proposed** |
| 0009 | `Apache-2.0 OR MIT` / GPL-2.0 kernel+BPF / CC-BY-4.0 / DCO | **Proposed — legal review** |
| 0010 | Build-heavy / deferred validation | **Proposed** |

## Next development block (recommended): Phase 1 / Block 1.1 "Measurement foundation"

Goal: be able to make *one* trustworthy statement about one change on one
machine. Scope (layer mode, Ubuntu 26.04; details in `state/roadmap.md`):

1. `ailx-probe`: hardware profile + environment fingerprint (versioned schemas).
2. Manifest schemas: workload, result, knob (draft).
3. Harness core + adapters: llama-bench, `vllm bench serve`/GuideLLM.
4. Statistics: RMIT scheduling, bootstrap CIs, effect sizes, A/A analysis.
5. Telemetry: NVML energy/power, privileged RAPL collector, PSI, wall-meter logger.
6. Quality gate v0: llama.cpp KLD + top-token agreement vs stored reference.
7. Local result store; report generator that only cites result IDs.

Acceptance: unit-tested with synthetic data; A/A analysis implemented; runs
CPU-only in CI; ready for HW-0…HW-2 in `architecture/hardware-test-plan.md`.

## Blockers / needed from maintainers

- Review/accept ADRs 0005–0010; legal review for 0009 (repo has no licence yet).
- Decide Phase 1 language (proposal: Python for harness) — open-questions A5.
- Procure a logging wall meter (energy ground truth) — A4.
- Grant push access for the agent (GitHub App) and wider network access for
  verification (`research/00-method.md`).

## Where to look

`INDEX.md` (map) · `architecture/overview.md` · `architecture/benchmarking.md` ·
`architecture/hardware-test-plan.md` · `research/07-opportunity-matrix.md` ·
`state/hypotheses.md` · `state/risks.md` · `state/open-questions.md`
