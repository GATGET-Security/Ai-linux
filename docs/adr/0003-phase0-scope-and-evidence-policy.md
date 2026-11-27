# ADR-0003: Phase 0 is research-only; evidence policy for all performance claims

> Status: Accepted · Date: 2026-11-26 · Deciders: Phase 0 lead architect (per project brief)

## Context

The project's value proposition is *measured* performance at unchanged quality.
The AI/Linux performance space is full of anecdotal tuning advice and vendor
marketing. Premature implementation (e.g. building a distribution) would lock
in choices before evidence exists.

## Decision

1. Phase 0 produces only research, architecture, plans and benchmark designs.
   No distribution image and no production code are built.
2. Evidence policy (applies in all phases):
   - No invented benchmark numbers. Numbers require an external source with
     context, or a result ID from the project's own benchmark store.
   - Sources carry URL, date, access date and an evidence grade A–D
     (`docs/research/00-method.md`).
   - Conjectures are labeled hypotheses and registered in
     `docs/state/hypotheses.md` with a falsification test.
   - Decisions (ADRs) need at least one A/B-grade source or own measured data.
   - Contradictory evidence is recorded, not suppressed.

## Consequences

- Slower start; much lower risk of building on folklore.
- Phase 0 documents will contain many hypotheses and few hard numbers. That is
  intended.

## Validation / revisit triggers

Phase 0 ends when the maintainers accept the Phase 1 plan
(`docs/state/roadmap.md`) and the next-block definition in `docs/CURRENT.md`.
