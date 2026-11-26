# Project Status

> Last updated: 2026-11-26 · Phase: **0 — Research & Architecture (deliverables complete, awaiting maintainer review)**

## Deliverables of Phase 0

| Deliverable | Location | State |
|-------------|----------|-------|
| Agent instructions (canonical) + Claude pointer | `AGENTS.md`, `CLAUDE.md` | done |
| Hot context and index | `docs/CURRENT.md`, `docs/INDEX.md` | done |
| Research method, evidence grading, access limits | `docs/research/00-method.md` | done |
| Landscape analysis | `docs/research/01-landscape.md` (82 sources) | done |
| Kernel/OS research | `docs/research/02-kernel-and-os.md` (62 sources) | done |
| Hardware, I/O, model loading | `docs/research/03-hardware-io-and-model-loading.md` (90 sources) | done |
| Runtimes and workloads | `docs/research/04-runtimes-and-workloads.md` (78 sources) | done |
| Base distributions and licensing | `docs/research/05-base-distributions-and-licensing.md` (97 sources) | done |
| Self-optimization and benchmarking | `docs/research/06-self-optimization-and-benchmarking.md` (77 sources) | done |
| Opportunity matrix (prioritized) | `docs/research/07-opportunity-matrix.md` | done |
| Architecture (overview, HW abstraction, self-optimization, benchmarking, test plan, delivery, community) | `docs/architecture/` | done (proposal) |
| ADRs 0001–0004 | `docs/adr/` | Accepted |
| ADRs 0005–0010 | `docs/adr/` | **Proposed — need maintainer review** |
| Hypotheses (53), risks (18), open questions, roadmap | `docs/state/` | done |

## Validation status

- **Nothing has been validated on hardware.** No benchmarks were run; no code
  exists. Every performance statement in the repository is either an external
  claim (with source and context), a theoretical bound (labeled), or a
  hypothesis.
- Validation ledger: empty (`docs/state/validation-ledger.md`).

## Known limitations of Phase 0

- Restricted network in the research sandbox: several primary documentation
  hosts were unreachable and the shared web-search budget was exhausted
  towards the end; some facts are marked **PK** (prior knowledge) or were seen
  only as search snippets (`docs/research/00-method.md`, RK-17).
- The repository branch could not be pushed from the research session because
  the GitHub App had no write access to the repository at that time; the
  commits exist locally on branch `claude/ai-linux-research-architecture-1bc3lh`.

## Next

Phase 1 / Block 1.1 "Measurement foundation" — see `docs/CURRENT.md` and
`docs/state/roadmap.md`.
