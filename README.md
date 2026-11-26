# AI Linux (working name)

**An open-source, Linux-based compute platform optimized from the ground up for
local AI workloads** — LLMs, image/video/audio generation, embeddings,
autonomous agents, training and fine-tuning.

> **Status: Phase 1.1 — Measurement foundation implemented.** The benchmark
> harness `ailx-bench` (Python, `python/`) exists and is tested in CPU-only CI;
> **nothing has been measured on the reference hardware yet**. There is no
> distribution image. Next: the first hardware run
> ([runbook](docs/runbooks/first-hardware-run.md)).

## Why

Running AI locally today usually means: a general-purpose distribution, a
vendor driver, and one or more inference tools with hand-copied tuning advice.
Nobody systematically measures whether kernel, scheduler, memory, I/O, driver
and runtime settings actually help *your* hardware and *your* workloads — or
whether a "speed-up" silently costs model quality, stability or security.

AI Linux aims to get **the maximum performance out of existing hardware at
unchanged model quality and system stability**, and to prove it with
reproducible measurements.

## What it is (planned)

- A **platform layer** ("ailx") that discovers the hardware, benchmarks
  workloads rigorously, tunes kernel/system/GPU/runtime settings in a closed
  loop, adopts only statistically significant, quality-preserving improvements,
  and rolls back regressions automatically.
- Later, an **AI Linux image** with a curated kernel, sched_ext scheduling
  policies, driver stacks (open by default, proprietary optional), atomic
  updates and boot-level rollback.
- **Hardware-agnostic** via provider plugins for NVIDIA, AMD, Intel, NPUs and
  future accelerators, reusing the best existing engines (llama.cpp, vLLM,
  SGLang, PyTorch, ComfyUI, whisper.cpp, …) instead of reinventing them.

## What it is not

- Not "Ubuntu with Ollama preinstalled".
- Not a new inference engine or compute API.
- Not a tuning script that disables CPU mitigations or IOMMU isolation for a
  few percent.

## Principles

AI-first · performance-first · local-first · hardware-agnostic · modular ·
community-driven · fully open-source own code.

Hard guardrails ([ADR-0004](docs/adr/0004-guardrails-security-quality-local-first.md)):
no disabling of security mechanisms for unproven gains, no silent quality
reduction, no forced telemetry or cloud dependency. Proprietary drivers are
optional, never required.

## Architecture at a glance

```
 Applications & agents     chat, IDEs, agents, image/video/audio apps, fine-tuning
 Runtime bundles (reused)  llama.cpp · vLLM · SGLang · PyTorch stacks · whisper.cpp · ONNX/OpenVINO
 AI Linux platform layer   probe · bench · optimize · knobs · guard · models · orchestrator · telemetry
 Base OS substrate         kernel + sched_ext · drivers · atomic image · boot assessment & rollback
 Hardware                  CPU/CCDs · GPU/VRAM · NPU · RAM · NVMe · network
```

Details: [docs/architecture/overview.md](docs/architecture/overview.md).

## Documentation

- [docs/CURRENT.md](docs/CURRENT.md) — current state and next development block
- [docs/INDEX.md](docs/INDEX.md) — map of all documents
- [docs/research/](docs/research/) — evidence-graded research (sources with URL, date, grade)
- [docs/architecture/](docs/architecture/) — architecture, benchmark methodology, hardware test plan
- [docs/state/](docs/state/) — status, roadmap, hypotheses, risks, open questions
- [docs/adr/](docs/adr/) — architecture decision records

## Contributing

The project is in its research phase. Contributions are welcome as issues or
pull requests against the documents: corrections with better sources, new
evidence, counter-evidence, and hardware you could benchmark on in Phase 1.
Agents and humans follow the same rules: see [AGENTS.md](AGENTS.md).

## Quick start (harness)

```bash
cd python && pip install -e ".[dev]"
ailx-bench probe                       # hardware profile + fingerprint
ailx-bench campaign all ../workloads/campaigns/synthetic-aa.json --report aa.md   # simulated self-test
```

## License

Own platform components: **MIT** (`LICENSE`). Kernel code and GPL-derived
components (none yet): **GPL-2.0-only** in separate directories. Third-party
components keep their licences. Documentation licence and a detailed legal
review are still open — see [LICENSING.md](LICENSING.md) and
[ADR-0009](docs/adr/0009-licensing-and-contribution-model.md).
