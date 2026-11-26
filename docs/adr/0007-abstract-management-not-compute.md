# ADR-0007: Abstract management, not compute — accelerator provider plugins

> Status: **Accepted** (maintainer decision 2026-11-26, including the Phase 0.1 review amendments below) · Date: 2026-11-26 · Deciders: maintainers

## Context

AI Linux must support NVIDIA, AMD, Intel, NPUs and future accelerators. There
are two ways to be hardware-agnostic:

- define a new portable compute layer (kernels, graph compiler, runtime), or
- reuse the portability that mature engines already provide (ggml backends,
  PyTorch device backends, ONNX Runtime execution providers, OpenVINO, Vulkan
  compute) and abstract only discovery, capability description, telemetry,
  controls and placement.

Evidence: the engine/backend landscape and its multi-vendor coverage is
documented in `docs/research/04-runtimes-and-workloads.md`; existing
platforms (Ollama, LM Studio, GPUStack, RamaLama, …) already do runtime
selection but not OS-level tuning or rigorous measurement
(`docs/research/01-landscape.md`).

## Options considered

1. **New compute abstraction** — maximal control; enormous effort; competes
   with well-funded upstreams; violates "reuse before reinvent".
2. **Single-engine bet** (e.g. only llama.cpp) — simple; excludes workloads
   (diffusion, training, high-throughput serving) and vendors the engine does
   not cover well.
3. **Provider plugins for management + pluggable runtime bundles for compute** —
   moderate effort; leverages upstream; risk of integration churn.

## Decision

Option 3. AI Linux defines:

- a versioned **provider interface** (discover, capabilities, driver status,
  telemetry, controls, runtime compatibility) — see
  `docs/architecture/hardware-abstraction.md`;
- **runtime bundles** that pin upstream engines per backend with SBOMs;
- a **placement planner** that maps workload × hardware → bundle + backend +
  placement, refined by the self-optimizer.

The core must contain no vendor-specific code; proprietary stacks are optional
providers/bundles. At least one fully open path (CPU and/or Vulkan) must exist
for every supported workload class where technically feasible.

## Consequences

- Fast time-to-value; quality of compute kernels comes from upstream.
- AI Linux depends on upstream release cadence and must manage version
  compatibility (bundle matrix, re-validation per ADR-0010).
- Contributions of performance improvements to engines go upstream, not into
  forks, wherever possible.

## Validation / revisit triggers

Revisit if measurements show that a required optimization cannot be achieved
without owning a compute layer (e.g. cross-vendor model splitting at scale).

## Phase 0.1 review (2026-11-26)

**Recommendation:** Accept with amendment. Status changes remain a maintainer decision.

Amendments proposed by the review (`docs/research/08-architecture-review.md` §3):

1. The capability model uses **memory domains** (discrete, unified, host);
   unified-memory APUs/SoCs are in scope, and their GPU-usable memory limit
   (TTM) is a knob, not a constant [V-11]
   (`docs/architecture/hardware-abstraction.md`).
2. Before freezing the provider schema, evaluate CDI/DRA-style device
   descriptions for reuse (review T8).

## Maintainer decision (2026-11-26)

Accepted together with the Phase 0.1 review amendments above; the amendments are part of the decision.
