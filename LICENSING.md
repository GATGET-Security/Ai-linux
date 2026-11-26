# Licensing

> Status: per ADR-0009 (maintainer decision 2026-11-26) · Legal detail review is still open before any public release

| Part of the repository | Licence | Notes |
|------------------------|---------|-------|
| Own, independent platform components (`python/`, `schemas/`, `helpers/`, `tools/`, `workloads/`, CI configuration) | **MIT** (`LICENSE`) | SPDX header `SPDX-License-Identifier: MIT` in every source file |
| Kernel code and components derived from GPL-2.0 code (kernel patches/modules, sched_ext BPF schedulers, userspace derived from `sched-ext/scx`) | **GPL-2.0-only** | None exist yet; such code will live in separate directories with their own `LICENSE` file |
| Third-party components (llama.cpp, vLLM, NVIDIA drivers/CUDA, models, …) | Unchanged, their own licences | Not vendored in this repository; fetched or built by the user |
| Documentation (`docs/`, `*.md`) | Proposed CC-BY-4.0, **not yet decided** | Until decided, documentation is covered by no separate grant beyond the repository's licence file for code |

Contributions: Developer Certificate of Origin sign-off is proposed (ADR-0009),
not yet enforced.
