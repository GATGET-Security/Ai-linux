# CURRENT — hot context

> Last updated: 2026-11-27 (Phase 1.5) · Keep ≤ 100 lines · Detail lives in linked docs (ADR-0002)

## Where we are

- **Phase 1.5 AI Compute Engine foundation: implemented (build-heavy), CPU/CI-tested.**
  `ailx caps | model | recommend | run | tune`: capability engine, GGUF/safetensors analysis,
  placement planner, managed llama-server runtime with admission control, autotuning on the
  campaign stack. Spec: `architecture/compute-engine.md`; ADR-0013 (proposed).
- **Earlier phases.** Phase 1.4 (`.deb`, experimental A/B image, QEMU-validated), 1.3 (`ailx`
  system layer) and 1.1 (`ailx-bench`) are done.
- **Phase 1.2 (first hardware run): deferred** (workstation unavailable). **Nothing is
  hardware-validated.**
  - Hardware rows `hw-queued`: VL-1.1-*, VL-1.3-*, VL-1.4-05…08, VL-1.5-01…06.
  - Prepared run: `tools/hw_validate.py` (runbook §8).

## What exists (code)

| Area | Where |
|------|-------|
| Compute engine: capabilities/providers, gguf/model, planner, runtime, tuning, CLI | `python/src/ailx/compute/` |
| `ailx` system layer: doctor, knobs, profiles, transactions, engines, image | `python/src/ailx/system/` |
| Measurement foundation (`ailx-bench`): campaigns, stats, store, KLD gate | `python/src/ailx/` |
| Schemas (+ capabilities, model-analysis, runtime-instance, tuning-record) | `schemas/` |
| `.deb`, reference image + QEMU test | `packaging/` |
| Hardware validation run + reference expectations | `tools/hw_validate.py`, `workloads/hw/reference-7950x3d-4090.json` |

## Decisions

- **Maintainer, 2026-11-26.**
  - ADRs 0005–0008 and 0010 accepted; ADR-0009 MIT / GPL-2.0-only.
  - Python harness; Ubuntu 26.04 + NVIDIA reference platform.
- **Maintainer, 2026-11-27.**
  - **ADR-0011 accepted.**
  - **ADR-0012 accepted as the experimental image architecture.**
  - AGENTS.md §11 change approved.
  - No final distro decision before the bake-off (ADR-0005).
- **Proposed, needs review: ADR-0013** (compute engine).
  - Engine defaults (llama.cpp `--fit`) are the baseline.
  - Our planner predicts for admission and tuning; adoption only after confirmation.

## Facts learned (CPU/cloud only — no reference hardware)

- llama.cpp 98cc730 fits layers itself (`--fit on`, 1024 MiB margin, `-ngl auto`). We reuse it
  rather than override it.
- Real llama-server under the runtime works (CPU). The memory prediction was 5–7× RSS for a 17 MB
  test model (status reading, no store ID): conservative and uncalibrated (H-R-11).
- On the noisy 4-vCPU VM, an **uncalibrated** tuning run adopted `fa-on`, while **calibrated** runs
  kept the baseline (A/A run CV 7–38 %, `ana-d6f34b2f…`/`ana-26890522…`, local cloud store). A/A
  calibration per tuning workload is therefore part of the validation run.
- `tuned` passes exactly the measured delta, not the planner's full flag set. Tuning records are
  untrusted input: settings are validated, and lossy ones need `--allow-lossy` at every start.

## Next development block

1. **When the workstation is available:** runbook §1–7, then §8 `tools/hw_validate.py` on stock
   Ubuntu and on the `hw` image (bake-off input). Update VL-1.5-*, H-R-11/12 and the other hardware
   rows.
2. **Without hardware:**
   - maintainer review of ADR-0013;
   - measured system-memory bandwidth (enables CPU/hybrid decode bounds);
   - managed vLLM;
   - VRAM broker / engine sleep hooks (H-R-05);
   - Bayesian refinement over the tuning records (Phase 2).

## Open (maintainers)

- ADR-0013; documentation and benchmark-data licence; DCO; legal review.

## Where to look

`INDEX.md` · `architecture/compute-engine.md` · `runbooks/first-hardware-run.md` §8 ·
`state/validation-ledger.md` · `architecture/reference-image.md` · `architecture/trust-boundary-review.md`
