# ADR-0004: Non-negotiable guardrails — security, quality, local-first, openness

> Status: Accepted · Date: 2026-11-26 · Deciders: Phase 0 lead architect (per project brief)

## Context

Performance-oriented distributions and tuning guides frequently recommend
disabling CPU vulnerability mitigations, IOMMU isolation, LSMs or Secure Boot,
and many "speed-ups" for AI workloads are lossy (quantization, lossy attention,
step caching). A self-optimizing system without hard limits would drift toward
such changes because they look good on throughput metrics.

## Decision

The following are invariants for every component, every default and every
automated tuning decision:

1. **Security**: no security mechanism is disabled or weakened to obtain a
   performance gain by default. Security-reducing knobs are classified
   *forbidden* or *explicit-opt-in* in the knob registry (see
   `docs/architecture/self-optimization.md`); the self-optimizer never
   proposes them autonomously.
2. **Quality**: no lossy change is adopted silently. Lossy techniques are
   labeled, opt-in, and shown with their measured quality delta. Automated
   adoption requires passing the quality gate defined in
   `docs/architecture/benchmarking.md`.
3. **Stability**: changes are adopted only after passing stability gates and
   with an automatic rollback path.
4. **Local-first**: all functionality works offline; no forced telemetry, no
   cloud account. Sharing (e.g. benchmark results) is opt-in and inspectable.
5. **Openness**: all project code is open source. Proprietary drivers and
   toolkits may be integrated as optional, clearly labeled components.

## Consequences

- Some headline numbers achievable by "unsafe" tuning will not be our defaults.
  We will document such knobs and their measured effect so that users can make
  informed decisions.
- The knob registry needs security and quality metadata for every knob.

## Validation / revisit triggers

Never relaxed by agents. Only maintainers can supersede this ADR, with public
discussion.
