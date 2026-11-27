# ADR-0009: Licensing set and contribution model

> Status: **Accepted as amended by maintainer decision 2026-11-26** (licence split below); detailed legal review remains open before any public release · Date: 2026-11-26 · Deciders: maintainers

## Context

All own code must be open source; proprietary drivers may be optional.
Research (`docs/research/05-base-distributions-and-licensing.md` Part 2) found:

- Apache-2.0 has an explicit patent grant but is not compatible with
  GPLv2-only kernel code; MIT is compatible but has no explicit patent grant
  [D-78][D-79][D-60]. `Apache-2.0 OR MIT` combines both (used e.g. by bootc)
  [D-34].
- In-kernel code must be GPL-2.0; sched_ext schedulers are BPF struct_ops
  programs and are rejected by the verifier unless they declare a
  GPL-compatible licence; userspace loaders may be permissive [D-57][D-58].
- DCO is the kernel's lightweight contribution certification; projects are
  moving from CLAs to DCO [D-84][D-85].
- CUDA/cuDNN/TensorRT and many model weights may not be redistributed freely
  [D-61][D-63][D-64][D-70][D-71].
- Single-vendor governance is a risk (Intel's abrupt end of Clear Linux) [L-26].

## Original proposal (superseded in part by the maintainer decision below)

| Artifact | Licence |
|----------|---------|
| Userspace tools, daemons, libraries, image recipes | `Apache-2.0 OR MIT` |
| Kernel patches and modules | `GPL-2.0-only` |
| BPF programs (sched_ext, struct_ops, LSM) | `GPL-2.0-only` (or `GPL-2.0-only OR BSD-2-Clause` where reuse in other kernels is wanted) |
| Documentation | `CC-BY-4.0`; code snippets in docs `MIT` |
| Configuration examples | `0BSD` or `MIT` |
| Published benchmark data | `CC-BY-4.0` (to be confirmed; CDLA-Permissive-2.0 as alternative) |

- Every file carries SPDX headers; the repository follows REUSE.
- Contributions use the **Developer Certificate of Origin** (`Signed-off-by`),
  enforced in CI; **no CLA**.
- Third-party proprietary components (NVIDIA userspace, CUDA, cuDNN,
  TensorRT, oneAPI parts) and model weights are **fetched on demand** from the
  vendor or distribution, never redistributed by default; model licences are
  recorded in model manifests and SBOMs and accepted explicitly by the user.
- Governance starts with founding maintainers under a published charter,
  moves to an elected steering council at roughly ten regular contributors,
  and evaluates a neutral foundation (e.g. LF AI & Data) later
  (`docs/architecture/community-and-licensing.md`).

## Consequences

- Maximal reuse by distributions and vendors; patent protection via the
  Apache-2.0 option; kernel/BPF code compatible with upstream.
- Two licence families in one repository → REUSE tooling and reviews needed.
- Until accepted, the repository has no licence (all rights reserved by
  default); this blocks external contributions to code, not to discussion.

## Validation / revisit triggers

Legal review outcome; foundation entry requirements; EU CRA steward status
[D-89][D-90][D-91].

## Phase 0.1 review (2026-11-26)

**Recommendation:** Keep Proposed until legal review; decide before the first code merge (Phase 1.1 prerequisite P1). Status changes remain a maintainer decision.

Amendments proposed by the review (`docs/research/08-architecture-review.md` §3):

1. Userspace code derived from the `sched-ext/scx` project inherits
   **GPL-2.0-only** (scx and e.g. `scx_layered` are GPL-2.0/GPL-2.0-only
   [V-17]); keep such code in a separate directory/package with its own
   licence boundary.
2. Model files and third-party model packages (OCI/ModelPack) keep their own
   licences; the model manifest records them.

## Maintainer decision (2026-11-26)

| Artifact | Licence | Notes |
|----------|---------|-------|
| Own, independent platform components (harness, schemas, helpers, tools, workloads, recipes) | **MIT** | Replaces the proposed `Apache-2.0 OR MIT`; SPDX headers in every source file; root `LICENSE` |
| Kernel code and components derived from GPL code (kernel patches/modules, sched_ext BPF schedulers, userspace derived from `sched-ext/scx`) | **GPL-2.0-only**, where licence law requires it | Kept in separate directories with their own `LICENSE` |
| Third-party components | **Unchanged** (their own licences) | Fetched on demand; never relicensed or redistributed by default |

Still open (not decided by the maintainers): documentation licence (proposal
CC-BY-4.0), benchmark-data licence, DCO enforcement, and the detailed legal
review before a public release. Overview: `LICENSING.md`.

Consequence of choosing MIT over `Apache-2.0 OR MIT`: no explicit patent
grant for own code; GPLv2 compatibility is preserved. Revisit if patent
concerns arise during legal review.
