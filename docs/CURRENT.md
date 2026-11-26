# CURRENT — hot context

> Last updated: 2026-11-26 (Phase 0.1) · Keep ≤ 100 lines · Detail lives in linked docs (ADR-0002)

## Where we are

- **Phase 0 + 0.1 review complete; awaiting maintainer decisions.** No code,
  no image, no measurements exist.
- Research: 6 evidence-graded documents (486 sources) + independent review
  (`research/08-architecture-review.md`, 18 sources, 17 claims re-checked
  against primary sources: 13 confirmed, 2 corrected, 1 sharpened, 1 caveat).
- Registers: 35 prioritized opportunities, 57 hypotheses, 22 risks.

## Confirmed facts that drive decisions (source-verified)

1. `SCHED_CACHE`: new in Linux 7.2, `default y`, active on multi-LLC CPUs,
   **debugfs-only** toggle; on a 7950X3D it packs a process into one CCD only
   while it averages **≤ 6 running threads** [V-01][V-02].
2. sched_ext: runtime attach/detach, automatic fallback to the fair class;
   BPF schedulers must be GPL-compatible; cgroup sub-schedulers **since 7.1**
   [V-03][V-04][V-06].
3. dmem cgroup reclaim **merged in 7.3**; amdgpu, xe, nouveau register
   regions; NVIDIA (out of tree) unclear [V-05].
4. Zen 4: ≈64 GB/s read per CCD (theoretical) < DDR5 dual-channel peak [V-10].
5. GPUDirect Storage: full mode only Data Center/Quadro; P2PDMA fails with
   KASLR, NVIDIA's workaround `nokaslr` is forbidden here → GDS deprioritized [V-09].

## Key hypotheses (untested; `state/hypotheses.md`)

- OS work matters for CPU/MoE offload, loading, tail latency, energy — not
  GPU-resident decode (H-L-01, H-S-02, H-D-03).
- `SCHED_CACHE` can hurt few-thread memory-bound processes; affinity bounds it
  (H-K-01, H-K-11). GPU power cap improves J/token (H-S-01).

## Architecture (proposal; `architecture/overview.md`)

Measured platform layer first, image later (ADR-0005); abstract management,
not compute, with memory domains incl. unified-memory devices (ADR-0007);
safety-tiered optimizer with FDR, probation, daemon coexistence and
lockdown-aware knobs (ADR-0008); service plane + sandbox; model files treated
as untrusted input.

## Decisions

| ADR | Decision | Status / review recommendation |
|-----|----------|-------------------------------|
| 0001–0004 | ADRs; AGENTS.md canonical; evidence policy; guardrails | Accepted |
| 0005 | Layer-first; image base by bake-off | Proposed → accept w/ amendments |
| 0006 | Ubuntu 26.04 GA-kernel baseline; arms U0/U1/U2/A | Proposed → accept w/ amendments |
| 0007 | Abstract management, not compute | Proposed → accept w/ amendment |
| 0008 | Safety tiers T0–T3 | Proposed → accept after amendments |
| 0009 | `Apache-2.0 OR MIT` / GPL-2.0 kernel+BPF / CC-BY-4.0 / DCO | Proposed → legal review first |
| 0010 | Build-heavy / deferred validation | Proposed → accept w/ amendment |

## Next development block: Phase 1.1 "Measurement foundation"

Executable spec: **`architecture/phase1.1-measurement-foundation.md`**
(Python for benchmark infrastructure; JSON Schemas + JSON-Lines sampler
protocol as language-neutral contracts). 8 work packages: schemas → probe →
runner/adapters → statistics (RMIT, BCa bootstrap, FDR, power) → telemetry
(NVML, PSI, privileged RAPL helper, meter adapter) → KLD quality gate →
content-addressed store/reports → CLI/CI/runbook.
Done = synthetic A/A and 5%-effect campaigns decide correctly in CPU-only CI;
tiny-model campaign end-to-end; no hardware claims.

## Blockers / needed from maintainers

- Licence decision or interim statement (ADR-0009) — prerequisite P1.
- Accept/amend ADRs 0005–0010 (open-questions A1, A8).
- Wall meter procurement (A4) — needed for Block 1.2, not 1.1.
- Wider network access for verifying remaining PK/snippet facts.

## Where to look

`INDEX.md` · `research/08-architecture-review.md` ·
`architecture/phase1.1-measurement-foundation.md` · `architecture/benchmarking.md` ·
`architecture/hardware-test-plan.md` · `state/hypotheses.md` · `state/risks.md`
