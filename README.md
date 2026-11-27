# AI Linux (working name)

**An open-source, Linux-based compute platform optimized from the ground up for
local AI workloads** — LLMs, image/video/audio generation, embeddings,
autonomous agents, training and fine-tuning.

> **Status: Phase 1.4 — Packaging & bootable prototype.** `ailx` (system
> layer) and `ailx-bench` (benchmark harness) ship as a native `.deb`; an
> **experimental** headless reference image (Ubuntu 26.04, A/B root slots,
> separate data partition) boots and updates/rolls back in QEMU — see the
> [validation report](docs/state/image-prototype-report.md). **Nothing has been
> measured on the reference hardware yet**; all profile settings are
> experimental and off by default. Next: the first hardware run
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

## Quick start

```bash
cd python && pip install -e ".[dev]"
ailx doctor                            # diagnosis with remedies
ailx status && ailx resources          # system-layer state, live CPU/RAM/GPU/VRAM
ailx profile plan inference --include-experimental   # what an (unvalidated) profile would change
ailx engine install llama.cpp --variant cpu          # pinned, isolated build with manifest
ailx-bench probe                       # hardware profile + fingerprint
ailx-bench campaign all ../workloads/campaigns/synthetic-aa.json --report aa.md   # simulated self-test
```

## Packaging and image (experimental)

```bash
python3 packaging/deb/build_deb.py --version 0.1.0-1 --out out/     # native .deb (Ubuntu 26.04)
sudo apt install ./out/ailx_0.1.0-1_all.deb
sudo python3 packaging/image/build_image.py --deb out/ailx_0.1.0-1_all.deb --version 1 \
     --authorized-key ~/.ssh/id_ed25519.pub --out out/              # A/B reference image (prototype)
python3 packaging/image/vm_test.py --disk out/ailx-1-vm.qcow2 --key ~/.ssh/id_ed25519 --quick --out report/
```

Details: [docs/architecture/reference-image.md](docs/architecture/reference-image.md).

## License

Own platform components: **MIT** (`LICENSE`). Kernel code and GPL-derived
components (none yet): **GPL-2.0-only** in separate directories. Third-party
components keep their licences. Documentation licence and a detailed legal
review are still open — see [LICENSING.md](LICENSING.md) and
[ADR-0009](docs/adr/0009-licensing-and-contribution-model.md).
