# ADR-0001: Record architecture decisions as ADRs

> Status: Accepted · Date: 2026-11-26 · Deciders: Phase 0 lead architect

## Context

AI Linux will be built by a changing set of humans and AI agents over a long
time. Decisions about kernels, base distributions, licensing and tuning policy
are expensive to reverse and must remain traceable to their evidence.

## Options considered

1. **No formal records** — fast, but decisions get lost in chat and commit logs.
2. **Lightweight ADRs in the repository** (Nygard-style) — small Markdown files,
   versioned with the code, reviewable in pull requests.
3. **External wiki / issue tracker only** — not versioned with the code; harder
   for agents working offline.

## Decision

Use numbered Markdown ADRs in `docs/adr/` based on `0000-template.md`.
Statuses: `Proposed`, `Accepted`, `Rejected`, `Superseded by ADR-XXXX`.
Accepted ADRs are never edited in substance; they are superseded.

## Consequences

- Every non-trivial decision links to evidence graded per `docs/research/00-method.md`.
- Agents may draft `Proposed` ADRs; only maintainers accept or reject them.

## Validation / revisit triggers

Revisit if the ADR count makes navigation hard (then add an ADR index by topic).
