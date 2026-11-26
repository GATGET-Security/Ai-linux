# Landscape: AI Distributions, Compute Platforms, Local-AI Runtimes and OS Research

> Status: Phase 0 research · Last updated: 2026-11-26 · Evidence grading: see 00-method.md

## Summary

1. **No surveyed project combines OS-level tuning, multi-vendor runtime selection, systematic per-machine benchmarking, quality verification and atomic rollback.** Each covers one to three of these (overlap matrix, F5). The *combination*, not any single component, is the credible niche for AI Linux.
2. **Vendor "AI distributions" are thin layers over a general-purpose OS.** RHEL AI 3.x is an inference-focused bootc image around a vLLM-based server; the InstructLab training workflow appears to have been dropped [L-02][L-04][L-05]. DGX OS is Ubuntu plus a pinned NVIDIA stack [L-16][L-17]. SUSE AI is a Kubernetes cluster platform [L-21]. None of them advertises AI-specific kernel or OS tuning as a feature (as far as found).
3. **Mainstream distributions now package the driver and toolkit layer.** Ubuntu 26.04 ships ROCm (7.1) and the CUDA toolkit in its archive [L-12][L-15], and Fedora packages ROCm [L-22][L-23]. Archive versions lag upstream [L-13]. Driver packaging alone is no longer a differentiator.
4. **Hardware-aware selection of runtime and model variant is commoditised.** Inference snaps, Lemonade, LocalAI, RamaLama and Docker Model Runner all detect hardware and choose an engine or image [L-09][L-33][L-53][L-24][L-48]. AI Linux should reuse one of them rather than build another.
5. **The clearest gap is closed-loop tuning with quality gates.** GPUStack auto-tunes concurrency, but it targets clusters [L-56]. RamaLama exposes `bench` and `perplexity` commands without a closed loop [L-24]. MLPerf Client (v2.0, August 2026) pairs latency with an MMLU accuracy check, but it is a benchmark, not a platform [L-63][L-64].
6. **Vendor-specific forks get abandoned; upstream code survives.** Intel ended Clear Linux with no transition period (July 2025) [L-26], archived IPEX-LLM (January 2026) [L-27] and retired IPEX in favour of upstream PyTorch [L-28]. Design rule: depend on paths that are upstreamed, and treat vendor extras as optional plugins.
7. **OS research on GPU multi-tenancy is strong on paper but not productised for workstations.** This covers LithOS, XSched, REEF and gpu_ext [L-65][L-66][L-67][L-68]. XSched is the most portable (ten XPUs, Apache-2.0) [L-66]. gpu_ext requires NVIDIA's open kernel modules [L-68]. These belong in an experimental, opt-in track only.
8. **The most directly reusable research for a local platform targets single-node memory hierarchies.** This includes KTransformers (CPU/GPU MoE), LMCache (tiered KV cache), ServerlessLLM (fast loading), and PowerInfer-2 and LLM in a Flash (flash offload) [L-74][L-79][L-76][L-71][L-72].
9. **Datacenter serving stacks already solve distributed inference.** llm-d reached 1.0 and NVIDIA Dynamo reached 1.6 [L-80][L-81][L-82]. Duplicating them would waste effort. Small local clusters are already addressed by exo-style projects [L-62].
10. **Openness varies sharply, so components must be curated.** The LM Studio GUI is proprietary [L-51]. Open WebUI added a branding clause [L-57]. NVIDIA NIM is free only for single-user RTX use [L-19]. A platform that promises "no forced cloud or telemetry" has to choose components by license and by their default behaviour.

## Scope

- **In scope:** AI-oriented OS distributions and vendor platforms; local-AI runtime and app platforms; OS and system research relevant to single-node or small-cluster local use; a synthesis of capability overlap and gaps.
- **Out of scope:** deep kernel mechanisms (02-kernel-and-os), runtime internals and quantization (04), base-distribution choice and licensing (05), benchmarking methodology (06).
- **Method:** desk research on 2026-11-26 with about 45 searches and fetches. No hands-on testing was done. Matrix cells are the author's assessment of the cited documentation; "?" marks cells that could not be verified.

## Findings

### F1. AI-oriented OS distributions and vendor platforms

| Project | Verified status (2026-11-26) | What it actually provides | Relevance for AI Linux |
|---|---|---|---|
| **Red Hat Enterprise Linux AI 3.x** | 3.0 shipped with Red Hat AI 3 (Oct 2025) [L-01]; 3.2 added AMD ROCm accelerators [L-03] | Positioned as a "generative AI inference platform". Ships as a bootc container image (`rhelai3/bootc-cuda-rhel9`) and runs models through Red Hat AI Inference Server, with a model optimization toolkit. There is no upgrade path from 1.5 to 3.0 [L-02][L-03] | Image-based OS with a curated accelerator stack is a reference design. It is enterprise-only and has a single runtime (vLLM) |
| **InstructLab** | Docs repository archived 2026-02-11; components split into separate repositories (e.g. training_hub) [L-05]. A Red Hat Jira entry states that RHEL AI 3.0 "dropped instructlab" [L-04] | Synthetic-data generation and fine-tuning workflow, now libraries | Signals that the vendor narrowed the product from "train and serve" to "serve". See Contradictions |
| **Red Hat AI Inference Server** | Product line 3.x | A hardened, supported vLLM distribution with LLM Compressor and a repository of validated third-party models [L-06]. Red Hat states that quantized models recover more than 99% of baseline accuracy (vendor claim, D as performance evidence) [L-07]. LLM Compressor was a Developer Preview in the 3.0 docs [L-08] | Only vendor found that ties quantized artifacts to a stated accuracy-recovery target. A model for "no silent quality reduction" |
| **NVIDIA DGX OS 7 / 8** | DGX OS 7 is based on Ubuntu 24.04; DGX OS 8 on Ubuntu 26.04; no in-place upgrade from 24.04 to 26.04 [L-18]. DGX Spark runs OS 7.4.0 (2026-02-02) with driver 580.126.09, CUDA 13.0.2 and kernel 6.17 [L-17] | Ubuntu plus NVIDIA kernel flavours, pinned driver families and over-the-air updates. Newer systems *require* the R580 open GPU kernel modules [L-16] | Pinned, validated driver-toolkit tuples are a good pattern. NVIDIA-only. The move to open kernel modules makes driver-level research such as gpu_ext feasible |
| **NVIDIA AI Enterprise / NIM** | Terms dated 2026-04 [L-19] | NIMs designated for RTX can run without a subscription only on a single-user PC or workstation; other uses need an AI Enterprise subscription [L-19] | Licence restrictions forbid relying on NIM by default; can only be an optional component |
| **Canonical Ubuntu** | 26.04 LTS: `apt install rocm` (ROCm 7.1, newer via SRU) [L-12]; `nvidia-cuda-toolkit` in archive, lagging NVIDIA's repo [L-15]; ROCm lag noted [L-13][L-14] | Inference snaps: beta Oct 2025, one snap per model, "automatically selects and deploys silicon-optimized runtime and model weights" [L-09][L-10]. 21 models listed in the repo, including Gemma 4, Qwen 3.x and Nemotron 3.x [L-10] | Closest mainstream analogue to "hardware-aware model deployment". A third party reports weak AMD coverage [L-11]. No OS tuning, benchmarking or quality reporting was found |
| **SUSE AI** | Launched Nov 2024; expanded Mar 2025 [L-20] | Cluster platform (SUSE AI Foundation plus AI Library). Pre-built NVIDIA drivers and operator; observability of token usage and GPU utilisation; guardrails [L-20][L-21] | Kubernetes-centric enterprise platform; little overlap with a single workstation |
| **Fedora AI/ML SIG and RamaLama** | ROCm packaged (6.4.4 in F43 per users; ROCm 8.x change proposed for F45) [L-22][L-23] | Distro-native ROCm and PyTorch packaging, still in progress [L-22]; RamaLama for container-based model runs (F3) [L-24] | Upstream-first packaging model. RamaLama is the strongest reuse candidate for the launcher |
| **Intel Clear Linux** | **Discontinued 2025-07-18**, effective immediately [L-26] | Was a performance-tuned distribution (compiler flags, function multiversioning) | Cautionary tale: a performance distribution maintained by one vendor died abruptly. Governance must not depend on a single sponsor |
| **CachyOS** | Active; 2026 kernel builds [L-34][L-35] | BORE scheduler by default; x86-64-v3, v4 and znver4 package builds; CPU level auto-detected at install [L-34][L-35] | Demonstrates that ISA-level rebuilds and scheduler tuning are possible in a community distribution. Its AI stack (ROCm/CUDA) was not documented in the sources found |
| **Pop!_OS 24.04** | Released Dec 2025 with COSMIC [L-36] | NVIDIA ISO; `system76-power` graphics modes including `compute` [L-37] | Good driver UX for NVIDIA laptops; no AI tuning |
| **Lambda Stack** | Official page lists Ubuntu 22.04 and 20.04 only [L-38] | Apt meta-package with pinned driver, CUDA and frameworks | Pattern of "pinned, tested tuple". 24.04 support is unconfirmed, so the project may be stagnating (weak evidence) |
| **NixOS ML ecosystem** | CUDA binary cache moved to `cache.nixos-cuda.org`; a forum post says there is no cache for stable branches [L-39][L-41]; Flox and NVIDIA arranged CUDA redistribution on Nix [L-40] | Declarative, reproducible ML stacks with generations (rollback) | Strongest reproducibility and rollback model. Cache instability means rebuild cost; nixified-ai status not found |
| **tinygrad / tinybox** | tinygrad 0.12 adds MI300/MI350 to its AM userspace driver [L-43] | The AM driver drives RDNA3/4 GPUs from userspace with `amdgpu` unloaded [L-42] | Inspiring minimal, vendor-neutral stack. Replacing the kernel driver conflicts with the stability principle, so it is not suitable as a default path |
| **"AI-native" distributions** | KORA announced Nov 2026; its "open" does not necessarily mean open source [L-44]. Several hobby distributions exist (Lisa OS, CX Linux, AnduinOS opt-in llama.cpp, AI-2, ThorOS) [L-45] | Assistants integrated into the desktop and shell | Focus on UX and agents, not on performance engineering. No systematic benchmarking found |

### F2. Silicon-vendor software stacks

- **AMD:** ROCm 7.2 (2026-01-21) widened Radeon and Ryzen support on Linux and Windows [L-31][L-32]. 7.2.1 added Ubuntu 24.04.4 and the 6.17 HWE kernel [L-32]. AMD sponsors **Lemonade**, an Apache-2.0 server with these backends [L-33]:
  - llama.cpp on Vulkan, ROCm, CUDA, Metal and CPU;
  - FastFlowLM on the XDNA2 NPU;
  - OpenAI-, Anthropic- and Ollama-compatible APIs;
  - Linux packages for Ubuntu, Debian, Fedora and Arch.
- **AMD and ComfyUI:** AMD now publishes ComfyUI-on-ROCm documentation for Linux [L-60]. Its speedup figures are vendor claims (D).
- **Intel:** Intel is consolidating on upstream PyTorch (XPU) and OpenVINO:
  - IPEX-LLM was archived on 2026-01-28 [L-27];
  - IPEX reached end of life in March 2026; Intel recommends using PyTorch directly [L-28];
  - OpenVINO remains active: 2026.4 on 2026-09-16, NPU compiler integration in 2026.0, and OpenVINO Model Server with OpenAI-style endpoints [L-29][L-30];
  - Canonical's inference snaps use OpenVINO to detect hardware and choose a variant [L-09].
- **NVIDIA:** NVIDIA controls the reference stack (DGX OS, NIM, Dynamo) and has moved newer platforms to open GPU kernel modules [L-16]. Commercial terms remain restrictive [L-19].
- **Implication:** a hardware-agnostic platform should target the vendor-neutral or upstream layers: Vulkan, upstream PyTorch device backends, llama.cpp, vLLM and OpenVINO. Vendor extensions should be optional plugins that can be removed.

### F3. Local-AI runtime and application platforms

| Platform | Openness (as found) | What it abstracts | Where it stops |
|---|---|---|---|
| **Ollama** | Public repo; latest v0.40.18 (2026-11-24) [L-46] | Model registry and pull; CUDA, ROCm, Vulkan and MLX builds; concurrent requests; llama.cpp bumped regularly [L-46]. Blog (2026-06) says GGUF compatibility now comes "through llama.cpp" and Vulkan is on by default [L-47] | `ollama launch` also wires coding tools to Ollama **cloud** models [L-46], which blurs the local-first boundary. No OS tuning, benchmarks, quality gate or rollback |
| **LM Studio / llmster** | GUI proprietary; `lms` CLI, SDK and MLX engine MIT [L-51]; headless `llmster` daemon since 0.4.0 (2026-01-28) [L-50] | llama.cpp and MLX engines; several models at once; GUI model discovery [L-51] | Closed core; no system layer |
| **Jan** | Public repo; licence sources conflict (see Contradictions) | llama.cpp engine; since v0.8, a single router process instead of one server per model [L-52] | Desktop chat; no tuning or verification |
| **LocalAI** | Public repo (MIT per README) [L-53] | Detects system capability when installing a model and downloads the matching backend variant (CUDA 12/13, ROCm, oneAPI/SYCL, Metal, Vulkan, Jetson); backends signed with cosign since 4.3.0 [L-53] | No benchmarking, quality gate or OS layer |
| **llamafile** | Mozilla.ai; 0.10.0 (2026-03-20) rebuilt the core and added GPU support, now tracking llama.cpp [L-54][L-55] | One portable executable per model | No selection or tuning beyond llama.cpp defaults |
| **Docker Model Runner** | Docker Desktop and Engine feature [L-48] | Sends GGUF to llama.cpp (the default) and safetensors to vLLM; Diffusers on Linux with NVIDIA; OCI model packaging [L-48][L-49] | Container-scoped; no host tuning |
| **RamaLama** | containers org, OSS [L-24] | Detects GPU on first run and pulls an accelerator-specific image (CUDA, ROCm, Vulkan, Intel, CANN, MUSA, Asahi). Engines: llama.cpp (default), vLLM, MLX. Transports: Hugging Face, OCI and others [L-24][L-25]. Security defaults: rootless, model mounted read-only, no network in `run`, all capabilities dropped [L-24]. Has `bench` and `perplexity` commands [L-24] | Bench and perplexity are manual; no search, gate or rollback loop |
| **Lemonade** | Apache-2.0, AMD-sponsored [L-33] | Multiple backends including NPU; `lemonade backends` lists what the machine supports; the embeddable build "auto-optimizes" (mechanism undocumented) [L-33] | Vendor-leaning; no OS layer |
| **GPUStack** | Public repo [L-56] | Very broad accelerator support (NVIDIA, AMD, Ascend, Hygon, MThreads, Cambricon and others); auto-configures vLLM, SGLang and TensorRT-LLM; "Auto Tune" explores concurrency to find peak throughput or the highest concurrency within an SLO; GPU slicing; prefill/decode disaggregation [L-56] | Cluster and serving oriented; no quality gate or OS tuning found |
| **Harbor** | Public repo [L-58] | Docker Compose orchestration of local LLM services (Ollama, llama.cpp, vLLM, Open WebUI, ComfyUI and others) [L-58] | Developer convenience, explicitly not production |
| **Open WebUI** | BSD-3 plus a branding clause since v0.6.6 (Apr 2025); OSI status disputed [L-57] | Front end, RAG, pipelines | Application layer only |
| **ComfyUI** | Public repo [L-59] | Node-graph image, video and audio generation; ROCm support documented [L-60] | Application layer; no system awareness |
| **Pinokio** | Little verifiable information [L-61] | One-click installer and launcher for AI apps | Unverified |
| **exo** | Public repo [L-62] | Automatic device discovery; topology-aware model splitting; RDMA over Thunderbolt 5 on macOS; MLX backend [L-62] | Apple-centric; Linux GPU path not verified |

**Common boundary.** Every runtime platform stops at the process or container boundary. None of them, as far as found:
- tunes kernel or OS parameters such as CPU governor, NUMA or IRQ placement, huge pages, I/O scheduler or swap/zram policy;
- searches configurations per machine with benchmarks;
- verifies output quality after a change, beyond RamaLama's manual `perplexity`;
- rolls back a combined driver, runtime and model change atomically.

Container images, in RamaLama and Docker Model Runner, version only the userspace part.

### F4. OS and system research relevant to local use

| Work | Venue / status | Mechanism | Reuse value for AI Linux |
|---|---|---|---|
| **REEF** | OSDI'22 [L-67] | Microsecond-scale preemption by killing and restoring idempotent inference kernels; authors report <2% end-to-end overhead on an AMD GPU | Idea for priority preemption between interactive and batch work on one GPU |
| **XSched** | OSDI'25; Apache-2.0; ported to Windows; repo migrated to AtomGit [L-66] | Preemptible command-queue abstraction (XQueue) with a multi-level hardware model; adapted to ten XPUs on seven software platforms | **Most portable** candidate for opt-in multi-workload GPU and NPU arbitration |
| **LithOS** | SOSP'25 (CMU and Meta); Rust [L-65] | TPC-level scheduling; transparent kernel "atomization" with no source, compiler or PTX changes; hardware right-sizing; power management | Design inspiration, NVIDIA-specific. The preprint and camera-ready numbers differ (see Contradictions) |
| **gpu_ext / bpftime GPU** | arXiv Dec 2025 [L-68]; LPC 2025 talk [L-69] | eBPF hooks in the NVIDIA open GPU kernel modules (UVM page faults and prefetch); device-side eBPF compiled to PTX via bpftime. Authors report up to 4.8× throughput across inference, training and vector search (preprint claim) | Start with **read-only observability** of UVM and offload; programmable policies only once upstream status is clear |
| **AIOS** | COLM 2025 per README [L-70] | Agent "kernel" in userspace that schedules LLM, memory, storage and tool resources for agents; authors report up to 2.1× faster agent execution | Concept for agent-aware prioritisation; not an OS kernel |
| **PowerInfer-2** | arXiv 2024 [L-71] | Hot and cold neuron clusters split across NPU and CPU, with I/O pipelining from flash | Memory-constrained profiles; depends on sparsity-friendly models |
| **LLM in a Flash** | arXiv (Apple) [L-72] | Loads only predicted-active parameters from flash | Same; informs NVMe offload design |
| **FlexGen** | ICML 2023 [L-73] | Throughput-oriented offloading to CPU and disk | Batch or offline jobs on small VRAM |
| **KTransformers** | SOSP'25 [L-74]; integrated into SGLang [L-75] | AMX and AVX-512 CPU expert kernels, asynchronous CPU-GPU scheduling and expert deferral for MoE models | **High** for large MoE models on workstations (x86 with AMX for best path) |
| **ServerlessLLM** | OSDI'24 [L-76] | Checkpoint format optimised for loading, multi-tier loading, live migration; authors report 3.6–8.2× faster loading than Safetensors and PyTorch in microbenchmarks | Fast model switching on multi-model desktops |
| **Llumnix** | OSDI'24 [L-77] | Live migration of requests between instances | Low for a single node |
| **Mooncake / LMCache** | FAST'25 best paper [L-78]; LMCache preprint and PyTorch ecosystem [L-79] | KV-cache-centric disaggregation; LMCache tiers KV cache across GPU, CPU, disk and remote storage via a vLLM/SGLang connector | LMCache is directly reusable for local agents with long shared prefixes |
| **llm-d / NVIDIA Dynamo** | llm-d: CNCF Sandbox 2026-03, 1.0 in 2026-10 [L-80][L-81]; Dynamo v1.6.0 [L-82] | KV-aware routing, prefill/decode disaggregation, SLA-driven planner, session prefix index for agentic workloads | Reuse concepts only; the stacks are Kubernetes and datacenter scale |

### F5. Overlap matrix

Legend: ● core feature · ◐ partial or indirect · ○ not provided (as found) · ? not verified. Columns, left to right: OS/kernel tuning, driver management, multi-vendor accelerators, runtime selection, model management, auto-tuning, benchmarking, quality verification, rollback, multi-workload scheduling, distributed.

| Project | OS | Drv | Multi-vendor | Runtime sel. | Model mgmt | Auto-tune | Bench | Quality | Rollback | Sched | Dist |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RHEL AI 3.x + AI Inference Server | ◐ | ● | ◐ | ○ | ◐ | ○ | ? | ◐ | ◐ | ○ | ◐ |
| DGX OS 7/8 | ◐ | ● | ○ | ○ | ○ | ○ | ? | ○ | ? | ○ | ◐ |
| Ubuntu 26.04 + inference snaps | ○ | ◐ | ◐ | ● | ● | ◐ | ○ | ○ | ◐ | ○ | ○ |
| SUSE AI | ○ | ◐ | ○ | ◐ | ◐ | ○ | ○ | ○ | ? | ◐ | ● |
| Fedora + RamaLama | ○ | ◐ | ● | ● | ● | ○ | ◐ | ◐ | ◐ | ○ | ○ |
| CachyOS | ● | ? | ? | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Pop!_OS 24.04 | ◐ | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| NixOS ML | ○ | ◐ | ◐ | ○ | ○ | ○ | ○ | ○ | ● | ○ | ○ |
| Ollama | ○ | ○ | ● | ◐ | ● | ? | ○ | ○ | ○ | ◐ | ○ |
| LM Studio / llmster | ○ | ○ | ◐ | ◐ | ● | ? | ○ | ○ | ? | ◐ | ○ |
| Jan | ○ | ○ | ◐ | ◐ | ● | ○ | ○ | ○ | ○ | ○ | ○ |
| LocalAI | ○ | ○ | ● | ● | ● | ○ | ○ | ○ | ? | ◐ | ? |
| llamafile | ○ | ○ | ◐ | ○ | ◐ | ○ | ○ | ○ | ○ | ○ | ○ |
| Docker Model Runner | ○ | ○ | ◐ | ● | ● | ○ | ○ | ○ | ◐ | ○ | ○ |
| Lemonade | ○ | ○ | ● | ● | ● | ◐ | ○ | ○ | ○ | ○ | ○ |
| GPUStack | ○ | ◐ | ● | ● | ● | ● | ◐ | ○ | ? | ● | ● |
| Harbor / Open WebUI / ComfyUI | ○ | ○ | ◐ | ◐ | ◐ | ○ | ○ | ○ | ○ | ○ | ○ |
| exo | ○ | ○ | ◐ | ○ | ◐ | ○ | ○ | ○ | ○ | ○ | ● |
| llm-d / NVIDIA Dynamo | ○ | ○ | ◐ | ◐ | ◐ | ◐ | ◐ | ○ | ? | ● | ● |
| LithOS / XSched / REEF / gpu_ext (research) | ● | ◐ | ◐ | ○ | ○ | ○ | ○ | ○ | ○ | ● | ○ |
| MLPerf Client (benchmark) | ○ | ○ | ● | ○ | ○ | ○ | ● | ● | ○ | ○ | ○ |

Basis for the cells: F1–F4 sources. Rollback cells for bootc, snaps, OCI images and Nix generations reflect the general design of those technologies; their AI-stack behaviour was not re-verified in this session.

### F6. Gaps, and where AI Linux would merely duplicate existing work

**Gaps that no project covers:**
- **(G1) Closed loop with quality gate and full-stack rollback.** Nothing changes OS, driver or runtime parameters, measures the effect, checks quality against a reference and reverts atomically. The parts exist separately: GPUStack Auto Tune, RamaLama bench and perplexity, MLPerf Client's accuracy gate, and bootc or Nix rollback.
- **(G2) OS-level tuning tied to AI workloads.** CachyOS tunes generically. Vendor AI distributions pin stacks but publish no AI-specific OS tuning (as found).
- **(G3) Vendor-neutral hardware-capability description.** Each launcher re-implements detection with its own logic: snaps/OpenVINO, Lemonade, LocalAI, RamaLama and Docker Model Runner.
- **(G4) Multi-workload arbitration on one workstation GPU.** Examples: interactive LLM, image generation and fine-tuning at once. This exists only in research (XSched, LithOS, REEF).
- **(G5) Community, multi-vendor, image-based "AI workstation" OS with optional proprietary drivers.** RHEL AI is enterprise and narrow; DGX OS is NVIDIA-only; NixOS is generic.

**Pure duplication, to avoid:**
- another model manager, chat UI or OpenAI-compatible server (Ollama, LM Studio, LocalAI, Lemonade, Docker Model Runner, RamaLama, Jan);
- another generic performance distribution (CachyOS);
- another distributed serving framework (llm-d, Dynamo, GPUStack);
- another userspace GPU driver (tinygrad).

## Optimization / innovation opportunities

| ID | Level | Opportunity | Expected benefit | Feasibility | Risk | Effort | Evidence | Novelty |
|---|---|---|---|---|---|---|---|---|
| O-L-01 | platform | Closed loop "tune → benchmark → quality gate → commit or roll back" spanning OS, driver, runtime and quantization settings | H: fills G1; every change becomes measured and reversible | M: building blocks exist | M: measurement noise; search cost; proxy metrics | L | [L-24][L-56][L-63][L-02] | novel (combination) |
| O-L-02 | userspace | Use an existing hardware-aware launcher (RamaLama preferred for its security defaults; Lemonade/LocalAI as alternatives) as the front end; contribute upstream, do not fork | M: avoids duplication; fast time to value | H | L–M: upstream direction may diverge | S–M | [L-24][L-33][L-53] | established |
| O-L-03 | userspace | Shared, vendor-neutral schema for accelerator capability plus *measured* profile (bandwidth, latency, power), consumable by launchers | M: removes duplicated detection logic; enables O-L-01 | M | M: adoption by other projects | M | [L-09][L-24][L-33][L-53] | novel (not found elsewhere) |
| O-L-04 | platform | Quality-regression gate as the default for any quantization, runtime or driver change: perplexity or KL divergence against a reference, plus small task evaluations, per device | H: enforces "no silent quality reduction" | H | L–M: proxies can miss task-specific regressions | M | [L-07][L-24][L-63] | known-elsewhere (vendor validation, MLPerf accuracy gate); novel as a local default |
| O-L-05 | platform | Signed, image-based OS (bootc/OSTree or Nix) with *optional* proprietary driver layers and atomic rollback of kernel, driver, toolkit and runtime together | H: stability while experimenting; addresses G5 | M | M: Secure Boot, out-of-tree modules, NVIDIA redistribution terms | L | [L-02][L-16][L-39][L-40] | known-elsewhere (RHEL AI, NixOS); novel as multi-vendor community variant |
| O-L-06 | runtime | Local tiered KV cache and prefix-reuse service for agents (LMCache with CPU RAM and NVMe) | M–H: lower time-to-first-token in long-context agent loops (expected, not measured locally) | M | M: coupling to engine versions | M | [L-79][L-82] | known-elsewhere |
| O-L-07 | runtime | Hybrid CPU/GPU MoE plan selection: detect AMX/AVX-512, memory bandwidth and VRAM, then choose a KTransformers or llama.cpp offload plan | H for large MoE models on workstations (authors' claims) | M | M: best path is x86 AMX only; model-specific | M | [L-74][L-75] | known-elsewhere |
| O-L-08 | runtime | Fast model load and switch (ServerlessLLM-style format; NVMe- and page-cache-aware prefetch) | M: faster switching on multi-model desktops | M | L | M | [L-76] | known-elsewhere |
| O-L-09 | kernel/runtime | Opt-in multi-workload GPU arbitration (interactive LLM vs ComfyUI vs training) built on XSched | M–H for interactive tail latency (hypothesis H-L-04) | L–M | H: driver stability; limits of proprietary drivers | XL | [L-66][L-65][L-67] | known-elsewhere (research); novel as a product |
| O-L-10 | kernel | Read-only eBPF observability of GPU and UVM behaviour (gpu_ext/bpftime) to diagnose offload stalls | M: explains performance cliffs | L–M: NVIDIA open modules only; AMD equivalent unknown | M | L | [L-68][L-69] | known-elsewhere (research) |
| O-L-11 | platform | Opt-in, privacy-preserving sharing of measured "known-good" configurations per hardware (no default telemetry) | M: amortises tuning cost across users | M | M: privacy; manipulated submissions | M | [L-63] | known-elsewhere (partially) |
| O-L-12 | application | Agent-aware request prioritisation (interactive tool calls ahead of background batch) above the runtime | M | M | L | M | [L-70][L-82] | known-elsewhere |

## Hypotheses

- **Hypothesis H-L-01 (where the gains are):** On a given machine, runtime, quantization and offload configuration yields larger inference gains than OS or kernel tuning.
  - *Rationale:* every widely used platform ignores the OS yet is considered fast. CachyOS-style tuning mainly targets responsiveness.
  - *Test to falsify:* on three or more hardware classes, compare (a) the default distribution with the best runtime configuration against (b) a tuned OS with the default runtime configuration. Measure tokens per second, time to first token and p99 latency. The hypothesis is falsified if (b) ≥ (a).
  - *Risk:* if true, the kernel layer of AI Linux matters less than planned and the focus should shift to O-L-01, O-L-04 and O-L-07.
- **Hypothesis H-L-02 (closed loop pays off):** A tune, benchmark, quality-gate and rollback loop finds configurations better than the runtime defaults, with no measurable quality loss, on at least half of tested machines.
  - *Rationale:* runtime defaults are generic. GPUStack's Auto Tune exists because defaults are not optimal for throughput [L-56].
  - *Test to falsify:* prototype the loop over llama.cpp and vLLM parameters on a fixed model set. Report the improvement distribution with confidence intervals; check quality via perplexity or KL divergence against an FP16 reference.
  - *Risk:* the gains may be within measurement noise.
- **Hypothesis H-L-03 (shared capability schema):** At least five launchers duplicate hardware-detection logic, and a shared schema would be adopted by at least two of them.
  - *Rationale:* F3 shows independent detection in snaps, Lemonade, LocalAI, RamaLama and Docker Model Runner.
  - *Test to falsify:* code survey plus maintainer outreach with a prototype library. The hypothesis is falsified if fewer than two projects engage within six months.
  - *Risk:* effort spent with no adoption.
- **Hypothesis H-L-04 (workstation GPU arbitration):** XSched-style preemptive arbitration on one consumer GPU cuts interactive p99 latency substantially while interactive and batch jobs share the GPU, without raising crash rates.
  - *Rationale:* REEF, XSched and LithOS show this in research settings [L-66][L-65][L-67].
  - *Test to falsify:* llama.cpp chat combined with a ComfyUI batch on an NVIDIA and an AMD consumer GPU. Measure p99 time to first token, aggregate throughput and 24-hour stability.
  - *Risk:* driver instability; unsupported proprietary paths.
- **Hypothesis H-L-05 (full-stack rollback):** An image-based OS with layered optional drivers can roll back a failed driver and runtime upgrade reliably on consumer hardware, including NVIDIA with Secure Boot.
  - *Rationale:* RHEL AI's bootc image and NixOS generations exist [L-02][L-39], but consumer multi-vendor coverage is untested.
  - *Test to falsify:* 100 scripted upgrade and rollback cycles per hardware class. The hypothesis is falsified if any cycle leaves the system unbootable or without a GPU.
  - *Risk:* module signing and DKMS complexity.
- **Hypothesis H-L-06 (distro lag):** Accelerator stacks packaged by the distribution lag upstream long enough (months) to cost measurable performance or model support.
  - *Rationale:* Ubuntu's ROCm lag is reported [L-13][L-14], and newer ROCm in Fedora lands first in a preview COPR [L-22].
  - *Test to falsify:* track version lag for six months and benchmark the archive version against upstream on the same hardware and models.
  - *Risk:* if true, AI Linux needs its own fast channel, which adds maintenance burden.

## Contradictory or weak evidence

- **InstructLab status.** Red Hat AI 3 announced a customization toolkit "based on InstructLab" [L-01]. A Red Hat Jira entry says RHEL AI 3.0 "dropped instructlab" [L-04]. The upstream docs repository is archived [L-05]. Likely reconciliation (unverified): the CLI workflow left the RHEL AI image while parts continue as libraries.
- **Ollama's engine.** RamaLama warns that "Ollama models are no longer compatible with llama.cpp" [L-24]. Ollama's June 2026 blog describes GGUF compatibility "through llama.cpp" [L-47], and its releases regularly bump llama.cpp [L-46]. The two engines may have converged again; the current state is unclear.
- **LithOS figures.** The arXiv v1 reports a 13× tail-latency reduction versus MPS. The camera-ready abstract reports different hybrid-stacking figures (4.7×) [L-65]. Cite the camera-ready figures only.
- **PowerInfer-2 speedups** differ across arXiv versions [L-71].
- **Inference snaps on AMD.** Canonical says the snaps are "silicon-optimized" [L-09][L-10]. A third party reports barely any AMD coverage [L-11].
- **Licences.** Harbor is listed as MIT on Product Hunt and as Apache-2.0 on a SourceForge mirror. Jan is described as AGPL-3.0 in one review and Apache-2.0 on a docs mirror. Check the LICENSE files before reuse.
- **llm-d CNCF acceptance date.** 2026-03-12 on the CNCF project page; 2026-03-24 in the KubeCon announcement [L-80].
- **Lambda Stack** officially lists only Ubuntu 22.04 and 20.04 [L-38]. This is weak evidence of stagnation.
- **Snippet-only sources.** Several facts (RHEL AI release notes, Canonical blog, Ollama blog) were seen only as search-engine summaries because page fetches failed. Grades are kept, but confidence is lower.

## Open questions

1. Does any tool already auto-tune llama.cpp or vLLM parameters per machine (layers offloaded, batch size, threads, KV-cache type) with a quality check? None was found; this needs a targeted code search in Phase 1.
2. How do inference snaps choose engine and quantization internally (manifests, scoring)? The documentation host was unreachable.
3. Can bootc or OSTree images layer NVIDIA proprietary modules under Secure Boot in a way that a community project may redistribute (see the Flox/NVIDIA arrangement [L-40])?
4. How much does OS-level tuning actually affect local inference? No primary benchmark was found, which is why H-L-01 exists.
5. Is preemptive GPU arbitration feasible on *proprietary* NVIDIA and AMD consumer drivers, or only on open modules?
6. What is the current status of Pinokio and nixified-ai, and what are SUSE AI's 2026 changes? Thin or no evidence was found.
7. Would RamaLama, Lemonade or LocalAI maintainers accept a shared capability schema (O-L-03)?

## Access limitations

- **WebFetch failed with DNS errors (host not resolvable from the sandbox):** `canonical.com`, `docs.redhat.com`, `arxiv.org`, `ollama.com`. Each was tried once and not retried. Their content was taken from search-engine summaries or from GitHub mirrors.
- **WebFetch worked:** `github.com` (Lemonade, GPUStack, Ollama releases, RamaLama, canonical/inference-snaps).
- `docs.kernel.org` was not attempted; the brief already reported it as failing.
- About 45 searches and fetches were used. GuideLLM, the Intel oneAPI roadmap, Pinokio, nixified-ai and the Ollama telemetry policy were not researched in depth.

## Sources

All accessed 2026-11-26. "snippet" means the content was seen only via search-engine summary.

| ID | Title | URL | Date | Accessed | Grade | Notes |
|---|---|---|---|---|---|---|
| L-01 | Red Hat brings distributed AI inference to production AI workloads with Red Hat AI 3 | https://www.redhat.com/en/about/press-releases/red-hat-brings-distributed-ai-inference-production-ai-workloads-red-hat-ai-3 | 2025-10-14 | 2026-11-26 | B | Press release; snippet |
| L-02 | RHEL AI 3.0 release notes | https://docs.redhat.com/en/documentation/red_hat_enterprise_linux_ai/3.0/html/release_notes | n.d. | 2026-11-26 | A | snippet (fetch DNS fail) |
| L-03 | RHEL AI 3.2 release notes | https://docs.redhat.com/en/documentation/red_hat_enterprise_linux_ai/3.2/html/release_notes/rhelai-32-release-notes_release-notes | n.d. | 2026-11-26 | A | snippet; AMD ROCm support |
| L-04 | Red Hat Jira RHEL-148269 | https://issues.redhat.com/browse/RHEL-148269 | n.d. | 2026-11-26 | C | Issue tracker statement; snippet |
| L-05 | InstructLab GitHub organisation / docs repo | https://github.com/instructlab | 2026-02-11 (archive) | 2026-11-26 | B | snippet |
| L-06 | Red Hat AI Inference Server technical deep dive | https://www.redhat.com/en/blog/red-hat-ai-inference-server-technical-deep-dive | n.d. | 2026-11-26 | B | snippet |
| L-07 | Optimizing generative AI models with quantization | https://developers.redhat.com/articles/2025/08/18/optimizing-generative-ai-models-quantization | 2025-08-18 | 2026-11-26 | B | >99% recovery = vendor claim (D as perf evidence) |
| L-08 | Red Hat AI Inference Server 3.0 – LLM Compressor | https://docs.redhat.com/en/documentation/red_hat_ai_inference_server/3.0/html-single/llm_compressor/index | n.d. | 2026-11-26 | A | Developer Preview label; snippet |
| L-09 | Introducing silicon-optimized inference snaps | https://ubuntu.com/blog/canonical-releases-inference-snaps | 2025-10-23 | 2026-11-26 | B | snippet (canonical.com fetch failed) |
| L-10 | canonical/inference-snaps | https://github.com/canonical/inference-snaps | n.d. | 2026-11-26 | B | Fetched; model list |
| L-11 | Ubuntu's Inference Snaps Run Best on NVIDIA, Partially on Intel, and Barely on AMD | https://www.starryhope.com/linux/ubuntu-inference-snaps-hardware-guide-2026/ | 2026 | 2026-11-26 | C | Third-party review; snippet |
| L-12 | AI with AMD ROCm on Ubuntu: your questions answered | https://ubuntu.com/blog/amd-rocm-on-ubuntu | 2026-06-03 | 2026-11-26 | B | snippet |
| L-13 | Ubuntu Weekly Recipe 0912 (gihyo.jp) | https://gihyo.jp/admin/serial/01/ubuntu-recipe/0912 | 2026 | 2026-11-26 | C | Archive ROCm lag; snippet |
| L-14 | Canonical To Distribute AMD ROCm Libraries With Ubuntu 26.04 LTS | https://phoronix.com/news/AMD-ROCm-Ubuntu-26.04-LTS | 2025-12 | 2026-11-26 | C | snippet |
| L-15 | How to Install NVIDIA CUDA Toolkit on Ubuntu 26.04 | https://linuxize.com/post/how-to-install-nvidia-cuda-toolkit-on-ubuntu-26-04/ | 2026 | 2026-11-26 | C | Archive CUDA package; snippet |
| L-16 | DGX OS 7 User Guide – Release Notes | https://docs.nvidia.com/dgx/dgx-os-7-user-guide/release_notes.html | 2026 | 2026-11-26 | A | R580 open modules required; snippet |
| L-17 | DGX Spark Release Notes | https://docs.nvidia.com/dgx/dgx-spark/release-notes.html | 2026-02 | 2026-11-26 | A | OS 7.4.0 versions; snippet |
| L-18 | DGX OS 8 Release Guidance | https://docs.nvidia.com/dgx/dgx-os-8-user-guide/8.0.0/release-guidance.html | 2026 | 2026-11-26 | A | No 24.04→26.04 upgrade; snippet |
| L-19 | Product-Specific Terms for NVIDIA AI Products | https://www.nvidia.com/content/dam/en-zz/Solutions/license-agreements/enterprise-software/product-specific-terms-ai-products-omniverse-16042026.pdf | 2026-04-16 | 2026-11-26 | A | Licence text; snippet |
| L-20 | SUSE Empowers Enterprise Innovations with Expanded Capabilities in SUSE AI | https://www.suse.com/news/suse-empowers-enterprise-innovations-with-expanded-capabilities-in-suse-ai/ | 2025-03 | 2026-11-26 | B | snippet |
| L-21 | SUSE AI 1.0 introduction | https://documentation.suse.com/suse-ai/1.0/pdf/AI-intro_en.pdf | n.d. | 2026-11-26 | A | snippet |
| L-22 | Fedora AI/ML SIG wiki and 2026 meeting notes | https://fedoraproject.org/wiki/SIGs/AI-ML | 2026 | 2026-11-26 | B | snippet |
| L-23 | Fedora Change: ROCm 8.x | https://fedoraproject.org/wiki/Changes/ROCm_8.x | 2026 | 2026-11-26 | A | Change proposal; snippet |
| L-24 | containers/ramalama README | https://github.com/containers/ramalama | n.d. | 2026-11-26 | B | Fetched |
| L-25 | ramalama-serve(1) man page | https://www.mankier.com/1/ramalama-serve | n.d. | 2026-11-26 | B | snippet |
| L-26 | Intel axes Clear Linux… support ends effective immediately | https://tomshardware.com/software/linux/intel-axes-clear-linux-the-fastest-distribution-on-the-market-company-ends-support-effective-immediately | 2025-07 | 2026-11-26 | C | Reports Intel blog of 2025-07-18 |
| L-27 | intel/ipex-llm (archived) | https://github.com/intel/ipex-llm | 2026-01-28 | 2026-11-26 | B | snippet |
| L-28 | intel-extension-for-pytorch issue #867 (retirement) | https://github.com/intel/intel-extension-for-pytorch/issues/867 | 2025 | 2026-11-26 | B | EOL end of March 2026; snippet |
| L-29 | OpenVINO Release Notes | https://docs.openvino.ai/releasenotes | 2026-09-16 | 2026-11-26 | A | 2026.4; snippet |
| L-30 | Intel Releases OpenVINO 2026 With Improved NPU Handling | https://phoronix.com/news/intel-openvino-2026.0-released | 2026-02 | 2026-11-26 | C | snippet |
| L-31 | AMD ROCm 7.2 release notes (Radeon/Ryzen, Linux) | https://www.amd.com/en/resources/support-articles/release-notes/RN-AMDGPU-LINUX-ROCM-7-2.html | 2026-01-21 | 2026-11-26 | A | snippet |
| L-32 | ROCm release notes (7.2.x) | https://rocm.docs.amd.com/en/latest/about/release-notes.html | 2026 | 2026-11-26 | A | snippet |
| L-33 | lemonade-sdk/lemonade README | https://github.com/lemonade-sdk/lemonade | n.d. | 2026-11-26 | B | Fetched |
| L-34 | CachyOS/linux-cachyos README | https://github.com/CachyOS/linux-cachyos | 2026 | 2026-11-26 | B | snippet |
| L-35 | CachyOS: A customizable, fast Arch-based OS (Linux Magazine 302) | https://www.linux-magazine.com/Issues/2026/302/CachyOS | 2026 | 2026-11-26 | C | snippet |
| L-36 | Pop!_OS 24.04 LTS Released: A Letter From Our Founder | https://system76.com/blog/post/pop-os-letter-from-our-founder | 2025-12 | 2026-11-26 | B | snippet |
| L-37 | Graphics Switching (Pop!_OS) | https://system76.com/support/graphics-switch-pop/ | n.d. | 2026-11-26 | A | Vendor docs; snippet |
| L-38 | Lambda Stack | https://lambda.ai/lambda-stack-deep-learning-software | n.d. | 2026-11-26 | B | snippet |
| L-39 | NixOS Wiki – CUDA | https://wiki.nixos.org/wiki/CUDA | 2026 | 2026-11-26 | B | snippet |
| L-40 | Nix, Flox, Nvidia: Opening Up CUDA Redistribution on Nix | https://discourse.nixos.org/t/nix-flox-nvidia-opening-up-cuda-redistribution-on-nix/69189 | 2025 | 2026-11-26 | B | snippet |
| L-41 | Massive rebuild of packages that use CUDA despite caches | https://discourse.nixos.org/t/massive-rebuild-of-packages-that-use-cuda-despite-cuda-maintainers-and-nix-community-caches/72394 | 2025 | 2026-11-26 | D | Forum; snippet |
| L-42 | tinygrad AM Driver docs | https://docs.tinygrad.org/developer/am/ | n.d. | 2026-11-26 | B | snippet |
| L-43 | Tinygrad 0.12 Released With Mesa NIR/NAK Support | https://phoronix.com/news/tinygrad-0.12 | 2026 | 2026-11-26 | C | snippet |
| L-44 | KORA Announced As Linux Desktop OS | https://www.phoronix.com/news/KORA-OS | 2026-11 | 2026-11-26 | C | snippet |
| L-45 | 5 Linux Distros Bringing AI to Your Desktop | https://itsfoss.com/ai-focused-linux-distros/ | 2026 | 2026-11-26 | C | snippet |
| L-46 | ollama/ollama releases | https://github.com/ollama/ollama/releases | 2026-11-24 | 2026-11-26 | B | Fetched; v0.40.9–0.40.18 |
| L-47 | Improved performance and model support with GGUF (Ollama blog) | https://ollama.com/blog/improved-performance-and-model-support-with-gguf | 2026-06-05 | 2026-11-26 | B | snippet (ollama.com fetch failed) |
| L-48 | Docker Model Runner – Inference engines | https://docs.docker.com/ai/model-runner/inference-engines/ | 2026 | 2026-11-26 | A | snippet |
| L-49 | Docker Model Runner Integrates vLLM | https://www.docker.com/blog/docker-model-runner-integrates-vllm/ | 2025-11 | 2026-11-26 | B | snippet |
| L-50 | Introducing LM Studio 0.4.0 | https://lmstudio.ai/blog/0.4.0 | 2026-01-28 | 2026-11-26 | B | snippet |
| L-51 | LM Studio docs – lmstudio vs llmster vs lms | https://lmstudio.ai/docs/app/basics/lmstudio-vs-llmster-vs-lms | 2026 | 2026-11-26 | A | snippet; licence statement |
| L-52 | Jan docs – Local AI Engine (llama.cpp) | https://www.jan.ai/docs/desktop/local-engine/llama-cpp | 2026 | 2026-11-26 | A | snippet |
| L-53 | mudler/LocalAI README | https://github.com/mudler/LocalAI | 2026 | 2026-11-26 | B | snippet (several mirrors in results) |
| L-54 | llamafile Returns (Mozilla.ai) | https://blog.mozilla.ai/llamafile-returns/ | 2025 | 2026-11-26 | B | snippet |
| L-55 | Llamafile gets GPU support and a rebuilt core | https://www.helpnetsecurity.com/2026/03/20/llamafile-0-10-0-released/ | 2026-03-20 | 2026-11-26 | C | snippet |
| L-56 | gpustack/gpustack README | https://github.com/gpustack/gpustack | n.d. | 2026-11-26 | B | Fetched |
| L-57 | Open WebUI License | https://docs.openwebui.com/license/ | 2025-04 / 2026-04 | 2026-11-26 | A | Licence text; snippet |
| L-58 | av/harbor README | https://github.com/av/harbor | n.d. | 2026-11-26 | B | snippet |
| L-59 | Comfy-Org/ComfyUI | https://github.com/Comfy-Org/ComfyUI | n.d. | 2026-11-26 | B | snippet |
| L-60 | AMD x ComfyUI blog / ComfyUI on ROCm docs | https://www.amd.com/en/blogs/2026/amd-comfyui-advancing-professional-quality-generative-ai-ryzen-radeon.html | 2026 | 2026-11-26 | B | Speedups = vendor claim (D) |
| L-61 | Pinokio (AlternativeTo listing) | https://alternativeto.net/software/pinokio/ | n.d. | 2026-11-26 | D | Only source found |
| L-62 | exo-explore/exo | https://github.com/exo-explore/exo | 2026 | 2026-11-26 | B | snippet |
| L-63 | MLPerf Client | https://mlcommons.org/benchmarks/client | 2026 | 2026-11-26 | A | Benchmark spec page; MMLU accuracy gate; snippet |
| L-64 | MLPerf Client v2.0 Expands AI PC Benchmarking | https://hpcwire.com/off-the-wire/mlperf-client-v2-0-expands-ai-pc-benchmarking-with-image-generation-and-agentic-ai | 2026-08 | 2026-11-26 | C | snippet |
| L-65 | LithOS: An Operating System for Efficient ML on GPUs (SOSP'25) | https://www.pdl.cmu.edu/PDL-FTP/BigLearning/lithos_sosp25.pdf | 2025-10 | 2026-11-26 | A | arXiv 2504.15465 differs; snippet |
| L-66 | XSched: Preemptive Scheduling for Diverse XPUs (OSDI'25) | https://www.usenix.org/conference/osdi25/presentation/shen-weihang | 2025-07 | 2026-11-26 | A | snippet |
| L-67 | Microsecond-scale Preemption for Concurrent GPU-accelerated DNN Inferences (REEF, OSDI'22) | https://www.usenix.org/conference/osdi22/presentation/han | 2022-07 | 2026-11-26 | A | snippet |
| L-68 | gpu_ext: Extensible OS Policies for GPUs via eBPF | https://arxiv.org/abs/2512.12615 | 2025-12-20 (v2) | 2026-11-26 | B | Preprint; snippet (arxiv fetch failed) |
| L-69 | LPC 2025: eBPF offload to GPU with bpftime | https://lpc.events/event/19/contributions/2168/ | 2025-12 | 2026-11-26 | B | snippet |
| L-70 | AIOS: LLM Agent Operating System | https://arxiv.org/abs/2403.16971 | 2025 (v5) | 2026-11-26 | B | COLM 2025 per README; snippet |
| L-71 | PowerInfer-2: Fast LLM Inference on a Smartphone | https://arxiv.org/abs/2406.06282 | 2024-12 (v3) | 2026-11-26 | B | snippet |
| L-72 | LLM in a Flash | https://arxiv.org/abs/2312.11514 | 2024 | 2026-11-26 | B | Venue not verified; snippet |
| L-73 | FlexGen (ICML 2023, PMLR 202) | https://arxiv.org/abs/2303.06865 | 2023 | 2026-11-26 | A | snippet |
| L-74 | KTransformers: CPU/GPU Hybrid Inference for MoE Models (SOSP'25) | https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/ | 2025-10 | 2026-11-26 | A | snippet |
| L-75 | Accelerating Hybrid Inference in SGLang with KTransformers CPU Kernels | https://lmsys.org/blog/2025-10-22-KTransformers | 2025-10-22 | 2026-11-26 | B | snippet |
| L-76 | ServerlessLLM (OSDI'24) | https://www.usenix.org/conference/osdi24/presentation/fu | 2024-07 | 2026-11-26 | A | snippet |
| L-77 | Llumnix: Dynamic Scheduling for LLM Serving (OSDI'24) | https://usenix.org/conference/osdi24/presentation/sun-biao | 2024-07 | 2026-11-26 | A | snippet |
| L-78 | Mooncake: A KVCache-centric Architecture (FAST'25) | https://www.usenix.org/conference/fast25/presentation/qin | 2025-02 | 2026-11-26 | A | snippet |
| L-79 | LMCache: An Efficient KV Cache Layer for Enterprise-Scale LLM Inference | https://arxiv.org/abs/2510.09665 | 2025-10 | 2026-11-26 | B | Preprint; snippet |
| L-80 | llm-d (CNCF project page) | https://www.cncf.io/projects/llm-d/ | 2026-03 | 2026-11-26 | B | snippet |
| L-81 | llm-d 1.0 aims to ease production AI inference at scale | https://itbrief.co.uk/story/llm-d-1-0-aims-to-ease-production-ai-inference-at-scale | 2026-11-12 | 2026-11-26 | C | snippet |
| L-82 | NVIDIA Dynamo v1.6.0 release notes | https://docs.nvidia.com/dynamo/latest/reference/releases/v1-6-0 | 2026-10 | 2026-11-26 | A | snippet |
