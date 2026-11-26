# ADR-0002: AGENTS.md is canonical; tiered context with a compact hot context

> Status: Accepted · Date: 2026-11-26 · Deciders: Phase 0 lead architect

## Context

The project is explicitly designed to be developed with AI coding agents
alongside humans. Different tools read different files (`AGENTS.md`,
`CLAUDE.md`, editor rule files). Duplicated instructions drift. Long research
documents overflow agent context windows and dilute the important state.

## Options considered

1. One instruction file per tool, maintained in parallel — drifts.
2. **`AGENTS.md` canonical; tool-specific files are thin pointers** — single
   source of truth; `CLAUDE.md` imports it.
3. Put all instructions in `README.md` — mixes public overview with agent rules.

## Decision

- `AGENTS.md` is the canonical instruction file. `CLAUDE.md` (and any future
  tool-specific file) only points to/imports it.
- Context is tiered: **hot** (`AGENTS.md`, `docs/CURRENT.md` ≤ 100 lines),
  **warm** (`docs/INDEX.md`, architecture, ADRs, state), **cold**
  (`docs/research/*`). Hot files summarize and link; they never duplicate
  research detail.
- `docs/CURRENT.md` is updated at the end of every work session.

## Consequences

- Agents start with a small, accurate picture and pull detail on demand.
- Requires discipline: research detail must not creep into hot files
  (reviewers should check this).

## Validation / revisit triggers

If `docs/CURRENT.md` repeatedly exceeds 100 lines or is stale after merges,
introduce an automated check in CI (Phase 1+).
