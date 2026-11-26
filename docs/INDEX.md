# Documentation Index

> Last updated: 2026-11-26 (Phase 1.1) · Start with [`../AGENTS.md`](../AGENTS.md) and [`CURRENT.md`](CURRENT.md)

Context tiers (ADR-0002): **hot** = always read, **warm** = read per task,
**cold** = read on demand and cite by ID.

## Hot

| Document | Purpose |
|----------|---------|
| [`../AGENTS.md`](../AGENTS.md) | Canonical instructions for all agents and contributors |
| [`CURRENT.md`](CURRENT.md) | Current phase, key decisions, next development block |

## Architecture (warm)

| Document | Purpose |
|----------|---------|
| [`architecture/overview.md`](architecture/overview.md) | Layer model, components, data model, deployment modes, innovation focus |
| [`architecture/hardware-abstraction.md`](architecture/hardware-abstraction.md) | Accelerator provider model (NVIDIA/AMD/Intel/NPU/future), placement planner |
| [`architecture/self-optimization.md`](architecture/self-optimization.md) | Closed-loop tuning, knob registry, safety tiers, decision rules, rollback |
| [`architecture/benchmarking.md`](architecture/benchmarking.md) | Reproducible benchmark methodology with Ubuntu baseline |
| [`architecture/hardware-test-plan.md`](architecture/hardware-test-plan.md) | First tests: Ryzen 9 7950X3D + RTX 4090, i5-11500 Proxmox server |
| [`architecture/image-and-delivery.md`](architecture/image-and-delivery.md) | Layer-first delivery, base-distro bake-off, image/update/rollback, build pipeline |
| [`architecture/community-and-licensing.md`](architecture/community-and-licensing.md) | License set, governance, contribution and community strategy |
| [`architecture/phase1.1-measurement-foundation.md`](architecture/phase1.1-measurement-foundation.md) | **Executable specification of the next block** (schemas, WPs, acceptance criteria) |

## State and planning (warm)

| Document | Purpose |
|----------|---------|
| [`state/status.md`](state/status.md) | What exists, what is validated (nothing on hardware yet) |
| [`state/roadmap.md`](state/roadmap.md) | MVP roadmap and long-term research roadmap |
| [`state/hypotheses.md`](state/hypotheses.md) | Register of all hypotheses with falsification tests |
| [`state/risks.md`](state/risks.md) | Risk register |
| [`state/open-questions.md`](state/open-questions.md) | Consolidated open questions and contradictory evidence |
| [`state/validation-ledger.md`](state/validation-ledger.md) | Deferred-validation ledger (Phase 1.1 rows, hw-queued) |

## Runbooks and code (warm)

| Document | Purpose |
|----------|---------|
| [`runbooks/first-hardware-run.md`](runbooks/first-hardware-run.md) | Commands for HW-0 … HW-2 and E1 on the reference hardware |
| [`../python/README.md`](../python/README.md) | `ailx-bench` package |
| [`../schemas/README.md`](../schemas/README.md) | Canonical schemas and conformance corpus |
| [`../helpers/rapl-collector/README.md`](../helpers/rapl-collector/README.md) | RAPL helper, threat model |
| [`../LICENSING.md`](../LICENSING.md) | Licence split (ADR-0009) |

## Decisions (warm)

| ADR | Title | Status |
|-----|-------|--------|
| [0000](adr/0000-template.md) | Template | — |
| [0001](adr/0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted |
| [0002](adr/0002-agent-instructions-and-hot-context.md) | AGENTS.md canonical; tiered context | Accepted |
| [0003](adr/0003-phase0-scope-and-evidence-policy.md) | Phase 0 research-only; evidence policy | Accepted |
| [0004](adr/0004-guardrails-security-quality-local-first.md) | Guardrails: security, quality, local-first, openness | Accepted |
| [0005](adr/0005-layer-first-delivery-and-deferred-base-distro.md) | Layer-first delivery; base distro decided by bake-off | Accepted |
| [0006](adr/0006-ubuntu-lts-benchmark-baseline.md) | Ubuntu LTS as benchmark baseline | Accepted |
| [0007](adr/0007-abstract-management-not-compute.md) | Abstract management, not compute; provider plugins | Accepted |
| [0008](adr/0008-self-optimizer-safety-tiers.md) | Self-optimizer safety tiers and adoption rules | Accepted |
| [0009](adr/0009-licensing-and-contribution-model.md) | Licensing set and contribution model | Accepted as amended (MIT / GPL-2.0-only) |
| [0010](adr/0010-build-heavy-deferred-validation.md) | Build-heavy / deferred-validation strategy | Accepted |

## Research (cold)

| Document | Topic | Source prefix |
|----------|-------|---------------|
| [`research/00-method.md`](research/00-method.md) | Method, evidence grades, access limitations | — |
| [`research/01-landscape.md`](research/01-landscape.md) | Existing AI distributions, platforms, research projects; gaps | L |
| [`research/02-kernel-and-os.md`](research/02-kernel-and-os.md) | Scheduling, NUMA, memory, I/O, drivers, NPU, power, security knobs | K |
| [`research/03-hardware-io-and-model-loading.md`](research/03-hardware-io-and-model-loading.md) | Test hardware facts, bandwidth bounds, model loading, offload, distributed, energy | H |
| [`research/04-runtimes-and-workloads.md`](research/04-runtimes-and-workloads.md) | Engines, quantization, KV cache, compilers, multi-vendor, non-LLM workloads | R |
| [`research/05-base-distributions-and-licensing.md`](research/05-base-distributions-and-licensing.md) | Base distro comparison; licensing & community research | D |
| [`research/06-self-optimization-and-benchmarking.md`](research/06-self-optimization-and-benchmarking.md) | Auto-tuning, rollback, benchmark methodology, quality & energy metrics | S |
| [`research/07-opportunity-matrix.md`](research/07-opportunity-matrix.md) | Consolidated, prioritized opportunities | O |
| [`research/08-architecture-review.md`](research/08-architecture-review.md) | Phase 0.1 independent review: source verification, corrections, gaps, ADR assessment | V |
