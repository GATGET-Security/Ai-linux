# Research Method, Evidence Grading and Limitations

> Status: Phase 0 research · Last updated: 2026-11-26 · Applies to all `docs/research/*` documents

## Summary

- Phase 0 research was performed as desk research against public primary
  sources (official documentation, kernel/project repositories, papers, release
  notes), supplemented by secondary sources where primary ones were unreachable.
- Every source is graded **A–D**; every conjecture is labeled as a hypothesis.
- **No benchmark was run** in Phase 0. No number in this repository is our own
  measurement. Numbers quoted from external sources keep their context.
- The research environment had **restricted network access** (details below);
  some primary documentation hosts were unreachable and were substituted by
  mirrors or secondary reporting. This lowers confidence in a few findings,
  which are flagged in the respective documents.

## Research questions (from the Phase 0 brief)

| # | Question | Primary document |
|---|----------|------------------|
| Q1 | What do existing AI distributions, compute platforms and research projects cover; where are gaps and real innovation opportunities? | [01-landscape.md](01-landscape.md) |
| Q2 | Which kernel / OS mechanisms (scheduling, NUMA, cache, huge pages, tiering, DMA, io_uring, drivers, NPU, power) matter for local AI? | [02-kernel-and-os.md](02-kernel-and-os.md) |
| Q3 | What are the facts, data-path limits and loading/offload options on the first test hardware? | [03-hardware-io-and-model-loading.md](03-hardware-io-and-model-loading.md) |
| Q4 | Which runtimes, compilers, quantization and KV-cache techniques matter, and which are lossless? | [04-runtimes-and-workloads.md](04-runtimes-and-workloads.md) |
| Q5 | Which base distribution(s) are viable; what licensing and community model fits? | [05-base-distributions-and-licensing.md](05-base-distributions-and-licensing.md) |
| Q6 | How can a system safely self-optimize, and how do we benchmark rigorously? | [06-self-optimization-and-benchmarking.md](06-self-optimization-and-benchmarking.md) |
| Q7 | Which opportunities should be pursued first? | [07-opportunity-matrix.md](07-opportunity-matrix.md) |

## Evidence grades

| Grade | Meaning | Typical examples |
|-------|---------|------------------|
| **A** | Primary, authoritative | Official documentation/specifications, kernel source and in-tree docs, vendor datasheets, license texts, peer-reviewed papers, benchmark rule documents |
| **B** | Primary-adjacent | Project READMEs, release notes, maintainer blogs/mailing-list posts, arXiv preprints (not peer reviewed), LWN.net reporting on primary discussions |
| **C** | Secondary | Technical press, third-party benchmarks with partially disclosed methodology (e.g. Phoronix), conference talks without data |
| **D** | Anecdotal | Forums, Reddit, single-user reports, vendor marketing claims without methodology |

Rules:

1. A decision in an ADR should rest on at least one **A/B** source or on our
   own measured data. C/D sources may motivate hypotheses, not decisions.
2. Vendor performance claims are graded **D** unless methodology and
   configuration are published (then **C**), regardless of the vendor's
   authority on the product's *specifications* (which are **A**).
3. When sources disagree, both are recorded under "Contradictory or weak
   evidence" in the respective document.

## Conventions

- **Source IDs** are prefixed per document: `L` landscape, `K` kernel/OS,
  `H` hardware/I/O, `R` runtimes/workloads, `D` distributions/licensing,
  `S` self-optimization/benchmarking.
- **Opportunities**: `O-<prefix>-NN`, consolidated and prioritized in
  [07-opportunity-matrix.md](07-opportunity-matrix.md).
- **Hypotheses**: `H-<prefix>-NN`, consolidated with falsification tests in
  [../state/hypotheses.md](../state/hypotheses.md).
- **Levels** used for classification: *kernel*, *userspace* (system services,
  libraries, configuration), *runtime* (inference/training engines, compilers,
  kernels), *application* (workloads, agents, UIs), *platform* (cross-cutting
  project infrastructure).
- **Quality impact** of a technique: *lossless* (output identical up to
  floating-point reordering effects), *lossy* (changes the model's numerical
  behaviour by design), *configurable-lossy*.
- **Effort** sizes: S (days), M (weeks), L (1–3 months), XL (quarter+), for a
  team of 1–3 engineers. These are planning estimates, not commitments.

## How the research was conducted

1. The brief was decomposed into six topic clusters (Q1–Q6), each researched in
   parallel against a common template and evidence policy.
2. Each cluster was asked to verify version/status facts against current
   sources (training-data knowledge can be stale; research date 2026-11-26).
3. Results were consolidated by the lead architect into the opportunity matrix,
   the hypothesis register and the architecture documents. Where the
   consolidation adds judgment, it is labeled as such.

## Access limitations (research environment)

The research ran in a sandboxed cloud container with an allow-listed egress
proxy. Observed on 2026-11-26:

| Channel | Status | Effect |
|---------|--------|--------|
| Web search API | Working | Main discovery channel; returns snippets and URLs, i.e. the content of some sources was seen only as search summaries |
| Page fetch, `github.com` | Working | Repositories and READMEs could be read directly |
| Page fetch, `docs.kernel.org` | **Blocked (DNS not resolvable from sandbox)** | Kernel docs had to be read via GitHub mirrors of `Documentation/` or via LWN/secondary sources |
| Direct `curl` to `kernel.org`, `github.com` | **Blocked (proxy 403)** | No raw downloads, no cloning of external repos, no local experiments |
| Additional hosts | See "Access limitations" in each research document | Individual documents list the hosts that failed |

Consequences:

- Findings that rest only on search-result summaries are marked as such
  where it matters; their grade is capped at the grade of the underlying
  source, but confidence is lower.
- **No hands-on experiment** (building schedulers, running loaders) was possible.
  All performance statements are external claims or hypotheses.
- To lift these limits for Phase 1, the environment needs access to at least:
  `docs.kernel.org`, `kernel.org`, `lore.kernel.org`, `lwn.net`,
  `developer.nvidia.com`, `docs.nvidia.com`, `rocm.docs.amd.com`, `arxiv.org`,
  `huggingface.co`, `pypi.org`, plus the target hardware itself.
