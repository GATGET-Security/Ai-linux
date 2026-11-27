# Project Status

> Last updated: 2026-11-27 · Phase: **1.3 — Runtime foundation implemented (CPU-tested); Phase 1.2 hardware run deferred (workstation unavailable)**

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
| ADRs 0005–0008, 0010 | `docs/adr/` | Accepted (maintainer decision 2026-11-26) |
| ADR-0009 | `docs/adr/` | Accepted as amended: MIT own components, GPL-2.0-only kernel/derived |
| Hypotheses (53), risks (18), open questions, roadmap | `docs/state/` | done |
| **Phase 0.1 review** (source verification, corrections, gaps, ADR assessment) | `docs/research/08-architecture-review.md` | done |
| **Phase 1.1 executable specification** | `docs/architecture/phase1.1-measurement-foundation.md` | ready (blocked by prerequisites P1–P3) |
| Register updates after review: 57 hypotheses, 22 risks | `docs/state/` | done |

## Phase 1.3 outcome

- `ailx` CLI (status, hardware, resources, doctor, knobs, profile, confirm,
  rollback, history, engine, system) on the shared probe.
- Profiles experimental and inactive by default; forbidden knobs rejected;
  conflicting tuning services respected.
- Transactions: journal-before-write, read-back verification, automatic
  rollback, dead-man timer, drift detection, crash recovery (tested with a real
  killed process), single active profile.
- Privileged helper with independent allow-list (path traversal, symlink
  escape, value injection and power-limit raising are refused in tests).
- Engines: real llama.cpp CPU build (282 s, static, 5 targets) and a real pip
  engine install from PyPI with full RECORD verification; vLLM/CUDA hw-queued.
- System install/uninstall into isolated roots (sudoers validated by visudo,
  foreign files protected, modified files kept, purge).
- Campaign arms with profiles; fingerprints carry the platform state.
- Validated on hardware: **nothing yet** (VL-1.3-02 … 10 hw-queued).

## Phase 1.1 outcome

- All 8 work packages implemented (`docs/architecture/phase1.1-measurement-foundation.md` §8).
- Tests: schema conformance, probe fixture trees, statistics simulations
  (A/A false-claim rate, 5% effect power, RMIT vs sequential drift), telemetry,
  RAPL helper, store integrity, CLI end-to-end; tiny-model llama-bench campaign
  and KLD gate end-to-end with llama.cpp 98cc730 (CPU, random-weight test model).
- Two methodology corrections found by the own end-to-end runs (noise-floor
  definition, conservative power analysis).
- Validated on hardware: **nothing yet** (ledger rows VL-1.1-01 … 07 `hw-queued`).

## Phase 0.1 review outcome

- 17 decision-relevant claims checked against primary sources (mostly Linux
  source at 7.4-rc4 and tagged releases): 13 confirmed, 2 corrected
  (sched_ext sub-schedulers since 7.1; dmem reclaim merged in 7.3),
  1 sharpened (`SCHED_CACHE` window ≤ 6 running threads on 7950X3D),
  1 confirmed with caveat (GDS GeForce = inference from NVIDIA's wording).
- Architecture extended: memory domains/unified memory, service plane,
  sandbox, realtime class, model-file security, tuning-daemon coexistence,
  lockdown-aware knobs, pre-registration/FDR/output-length control.

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
- Push access was missing during most of the session; it was restored and the
  branch `claude/ai-linux-research-architecture-1bc3lh` is published.

## Next

Phase 1 / Block 1.1 "Measurement foundation" — see `docs/CURRENT.md` and
`docs/state/roadmap.md`.
