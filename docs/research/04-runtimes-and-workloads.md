# Runtimes, Quantization and Workloads: Application-Level Optimization

> Status: Phase 0 research · Last updated: 2026-11-26 · Evidence grading: see 00-method.md

Conventions used in this document (in addition to [00-method.md](00-method.md)):
**lossless** = output distribution mathematically unchanged (bitwise results may still differ
because of floating-point reduction order, batch shape or backend); **lossy** = changes the
model's numerical behaviour by design; **configurable-lossy** = lossless at some settings,
lossy at others. **PK** marks a statement taken from prior knowledge that could not be
re-verified in this session (see "Access limitations"); PK statements must not carry ADR
decisions on their own.

## Summary

- **Engine landscape is consolidating; build a pluggable-engine architecture, not a new engine.**
  llama.cpp/ggml reached v1.0.0 with semantic versioning (13 Nov 2026) and has the broadest
  backend list (CUDA, HIP, Vulkan, SYCL, OpenVINO, MUSA, CANN, WebGPU, CPU…) [R-02][R-03];
  vLLM v0.34.0 (16 Nov 2026) is CUDA-13-only plus ROCm 10.0 and XPU wheels [R-05]; HF TGI is in
  maintenance mode [R-08]; Intel IPEX / ipex-llm are archived in favour of upstream PyTorch XPU
  [R-46]; Ollama 0.30 runs GGUF through llama.cpp again [R-13].
- **RTX 4090 (Ada, sm_89) specifics:** FP8 is a supported precision on SM89 in TensorRT-LLM
  [R-09]; there is no native FP4 path on Ada (FP4 tensor-core paths are Blackwell-only)
  [R-20][R-64]; FlashAttention-3 is Hopper-only and FA4 targets Hopper/Blackwell, so Ada is
  limited to FA2 within the Dao-AILab family — FlashInfer and engine-native kernels also cover
  sm_89 [R-18][R-64].
- **Every weight or KV-cache quantization is lossy.** Accuracy/perplexity hide damage: Dutta
  et al. find up to 13.6 % answer "flips" at near-baseline accuracy and a 0.981 Spearman
  correlation between KL divergence and flips on MMLU [R-24]. A "no silent quality reduction"
  policy therefore needs KLD + flip-rate + long-context tests, not perplexity alone.
- **Kernel bugs can silently destroy quality:** vLLM reported a needle-in-a-haystack drop from
  91 % (BF16) to 13 % (FP8) caused by low-precision accumulation in an FP8 attention kernel,
  fixed later [R-07]. Continuous quality-regression gating must be a platform service.
- **Biggest lossless levers** for local use: prefix caching (RadixAttention / automatic prefix
  caching) for agents, paged/continuous batching, speculative decoding with correct verification
  (n-gram, draft model, EAGLE-3, MTP — all in llama.cpp now) [R-30][R-65][R-66], CPU/GPU MoE
  expert placement [R-54], CUDA Graphs, offloading instead of down-quantizing.
- **Diffusion/video:** lossless levers (offload, async weight streaming, FA2/SDPA) vs. lossy
  presets (SageAttention, SVDQuant/Nunchaku, TeaCache-style step caching) [R-19][R-43][R-52][R-58].
  Wan2.2 TI2V-5B is stated to run 720p on ≥24 GB (RTX 4090); A14B is stated as ≥80 GB without
  offload [R-59].
- **AMD consumer GPUs:** ROCm 10.0 (TheRock-based) lists RDNA3 (gfx110X) and RDNA4 (gfx120X)
  [R-33][R-34], and vLLM ships ROCm 10.0 artifacts [R-05]; secondary benchmarks report llama.cpp
  Vulkan beating ROCm on several AMD consumer setups [R-74]. ZLUDA is back to hobby status [R-36]
  and should not be a foundation.
- **NPUs on Linux became usable in 2026** (AMD XDNA2 via Lemonade/FastFlowLM, now under the ROCm
  org; Intel NPU via OpenVINO 2026.x + linux-npu-driver) [R-37][R-50] — suited to small,
  always-on models (ASR encoder, embeddings, small LLMs), usually in quantized formats (lossy).
- **One GPU, many workloads:** GeForce has no MIG; MPS is Linux-only, not officially confirmed
  for GeForce and weakens memory isolation [R-41][R-42]. Default should be time-slicing plus a
  VRAM broker using engine-level sleep/unload/offload (vLLM sleep mode [R-06], ComfyUI weight
  streaming [R-58]).
- **Fine-tuning on 24 GB:** QLoRA-style workflows (Unsloth, KTransformers SFT for MoE) are viable
  [R-40][R-54][R-71]; torchtune is no longer actively maintained [R-39].

## Scope

Runtime- and application-level optimization for local AI on Linux: LLM inference engines,
quantization, KV-cache/serving techniques, attention kernels, compilers and kernel DSLs,
non-LLM workloads (image, video, audio, embeddings, agents, fine-tuning), multi-vendor backends
including NPUs, and sharing one GPU among workloads. Kernel/OS mechanisms (scheduling, memory
tiering, drivers) are covered in 02-kernel-and-os.md; hardware data paths and model loading in
03-hardware-io-and-model-loading.md; benchmark methodology in 06-self-optimization-and-benchmarking.md.
No benchmark was run; all numbers are external claims with their context.

## Findings

### F1. LLM inference engines

**llama.cpp / ggml.** v1.0.0 of llama.cpp and ggml was released 13 Nov 2026 with a semver
contract for the public C API/ABI; secondary reports also mention an FP8 KV cache and a TTS
server endpoint in that release (single secondary source, unverified) [R-03]. ggml joined
Hugging Face in Feb 2026 [R-04]. The build guide lists CPU, BLAS, Metal, SYCL (Intel GPUs incl.
Arc/iGPU), CUDA, MUSA, HIP, Vulkan, CANN, ZenDNN, KleidiAI, OpenCL (Adreno), WebGPU (Dawn,
also browser), WASI, OpenVINO (Intel CPU/GPU/NPU) and Hexagon [R-02]. HIP builds target what
`rocminfo` reports; unsupported AMD GPUs need `HSA_OVERRIDE_GFX_VERSION` [R-02]. Server
(`llama serve` / `llama-server`) is OpenAI-compatible, MIT-licensed [R-01]. Speculative decoding
supports draft models, EAGLE-3, MTP heads, DFlash-family block-diffusion drafts and several
draft-less n-gram modes [R-30]. Focus: single-user latency, hybrid CPU+GPU, widest hardware.

**vLLM.** v0.34.0 (16 Nov 2026): Model Runner V2 is the only GPU runner; "extensible KV cache"
on by default, sized from memory free after warmup; default block size 64 [R-05]. v0.33.0 dropped
CUDA 12.9 (CUDA 13.0 only); v0.34.0 ships ROCm 10.0 only; XPU and CPU (x86_64/arm64) wheels are
published [R-05]. v0.32.0 extended NVFP4 KV-cache support to SM8x/9x/12x [R-05]. The V1 engine
architecture replaced V0 in 2025 (PK). Focus: batched throughput, OpenAI-compatible serving.

**SGLang.** RadixAttention keeps KV of all requests in a radix tree for prefix reuse [R-66].
v0.5.20 (Sep 2026) reportedly added Intel XPU to standard releases (secondary) [R-16]; an LMSYS
summit recap (17 Nov 2026) shows an "SGLang V1.0" keynote slide, release not confirmed [R-15].
AMD Instinct is supported; consumer RDNA not confirmed (open question).

**TensorRT-LLM.** NVIDIA-only. The support matrix lists Ada (SM89) with FP8, Blackwell (SM100/
SM103/SM120) with FP8/FP4; GPUs not listed are "expected to work" Volta–Ada but untested [R-09].
Latest PyPI release seen: 1.2.1 (20 Apr 2026), docs at 1.3.0rc* [R-09][R-10]. Not
verified this session whether the PyTorch backend has full SM89 FP8 coverage.

**ExLlamaV3.** EXL3 is "a streamlined variant of QTIP" (trellis-coded quantization); needs
PyTorch ≥2.6 and CUDA ≥12.4, targets consumer NVIDIA GPUs, offers TP/EP, 2–8-bit cache
quantization, MIT [R-11][R-12]. No AMD support stated [R-11]. ExLlamaV2 status: not verified.

**Others.** *MLC-LLM*: TVM-based, CUDA/ROCm/Vulkan/Metal/WebGPU/OpenCL, Apache-2.0; release
cadence unclear [R-55]. *KTransformers*: CPU/GPU heterogeneous MoE (AMX/AVX512/AVX2 kernels,
hot experts on GPU), SGLang integration, SFT via LlamaFactory, v0.7.0 (Aug 2026), Apache-2.0
[R-54]. *Ollama*: v0.17 introduced its own "Ollama engine" (secondary) [R-14]; v0.30
(blog, ~Jun 2026) runs GGUF through llama.cpp and MLX on Apple [R-13]; Vulkan available for
AMD/Intel [R-75]. *LMDeploy*: TurboMind + PyTorch engines, AWQ W4A16, online int4/int8 KV
quant, MXFP4, v0.13.0, Apache-2.0 [R-56]. *TGI*: maintenance mode, HF points to vLLM/SGLang
[R-08]; repo archival (Mar 2026) only reported by a third party [R-81]. *Modular MAX*: 26.2
(Mar 2026) runs common models on AMD RDNA consumer GPUs incl. iGPUs; split license (Apache-2.0
w/ LLVM exceptions for the open repo, Modular Community License for MAX/Mojo use) [R-17].

**Support matrix** (✓ = documented, ~ = partial/experimental/secondary, – = not documented)

| Engine | NVIDIA | AMD | Intel | Other | Main quant formats | Focus | License |
|---|---|---|---|---|---|---|---|
| llama.cpp | ✓ CUDA, Vulkan | ✓ HIP, Vulkan | ✓ SYCL, Vulkan, OpenVINO | Metal, CANN, MUSA, WebGPU, CPU [R-02] | GGUF k/i-quants, Q8_0, MXFP4 (~) | single-user, hybrid offload | MIT [R-01] |
| vLLM | ✓ CUDA 13 | ✓ ROCm 10 | ✓ XPU | CPU, plugins (Ascend) [R-05] | FP8, NVFP4, AWQ/GPTQ (PK), GGUF (~, PK) | batched throughput | Apache-2.0 (PK) |
| SGLang | ✓ | ✓ Instinct; RDNA ? | ~ XPU [R-16] | – | FP8, FP4, AWQ/GPTQ (PK) | throughput, agents, prefix reuse | Apache-2.0 (PK) |
| TensorRT-LLM | ✓ only | – | – | – | FP8 (SM89+), FP4 (SM100/120) [R-09] | max NVIDIA perf | Apache-2.0 (PK) |
| ExLlamaV3 | ✓ only | – | – | CPU offload [R-11] | EXL3 (QTIP-based) | single-user, low bpw | MIT |
| MLC-LLM | ✓ CUDA/Vulkan | ✓ ROCm/Vulkan | ✓ Vulkan | Metal, WebGPU, OpenCL [R-55] | own q*f16 (PK) | portability | Apache-2.0 |
| KTransformers | ✓ | ~ ROCm | ~ Arc | Ascend; CPU AMX/AVX [R-54] | INT4/INT8 CPU experts | huge MoE on 1 GPU | Apache-2.0 |
| Ollama | ✓ | ✓ (ROCm/Vulkan) | ~ Vulkan | MLX [R-13][R-75] | GGUF | ease of use | MIT (PK) |
| LMDeploy | ✓ | ~ (reqs file) | – | Ascend, MACA [R-56] | AWQ, KV int4/8, MXFP4 | throughput | Apache-2.0 |
| TGI | maintenance [R-08] | – | – | – | – | do not adopt | Apache-2.0 (PK) |
| Modular MAX | ✓ | ✓ Instinct, ~ RDNA [R-17] | – | Apple (~) | own | portable compiler stack | split [R-17] |

### F2. Quantization

| Technique | What it does | HW acceleration | Quality class | Evidence |
|---|---|---|---|---|
| GGUF k-quants (Q2_K–Q6_K), Q8_0 | block-wise weight-only, dequant in kernel | all ggml backends | lossy (Q8_0 ≈ near-lossless) | KLD table [R-23] |
| GGUF i-quants + imatrix | codebook/importance-weighted, calibration data | all ggml backends | lossy; imatrix reduces loss at low bpw | [R-23] |
| AWQ (W4A16), GPTQ | activation-aware / 2nd-order PTQ | Marlin-type kernels Ampere+ (PK) | lossy | [R-68][R-69] |
| EXL2 / EXL3 | variable bpw; EXL3 = QTIP variant (trellis) | NVIDIA only | lossy | [R-11][R-12] |
| HQQ | calibration-free half-quadratic | various (PK) | lossy | [R-70] (PK) |
| bitsandbytes NF4 / int8 | weight-only, used in QLoRA | NVIDIA, others partial (PK) | lossy | [R-71] |
| FP8 E4M3 (W8A8) | 8-bit float weights+activations | Ada SM89, Hopper, Blackwell [R-09] | lossy (usually small) | [R-09] |
| NVFP4 / MXFP4 | 4-bit float, block scales (16/32) | Blackwell only; SM120 via different ISA path than SM100 [R-20][R-21] | lossy | [R-20][R-22] |
| KV cache FP8 / q8_0 / q4_0 / int4 / NVFP4 | quantized K/V | engine-specific | lossy, long-context sensitive | [R-07][R-25][R-26] |
| SVDQuant (W4A4 + low-rank branch) | diffusion models | INT4 Turing–Ada, NVFP4 Blackwell [R-52] | lossy | [R-52][R-53] |

Evidence on quality loss and measurement:
- llama.cpp's `llama-perplexity` computes KL divergence vs. stored FP16 logits, plus percentiles
  of token-probability change; for LLaMA-3-8B (no imatrix) the README lists mean KLD ≈0.0055
  (Q6_K) and ≈0.0108 (Q5_K_M) — source's claim, Wikitext-2 [R-23]. Logit files are large
  (~37 GiB for LLaMA-3 on Wikitext-2) [R-23].
- Dutta et al. (NeurIPS 2024): all schemes except GPTQ W8A16 show many flips (up to 13.6 %)
  while accuracy stays close to baseline; KLD tracks flips (ρ = 0.981 on MMLU) [R-24].
- KV quantization: long inputs (4k+) are more sensitive; for some model families even KV8 hurts
  long texts, for others KV8 is near-lossless and KV4 degrades [R-25]; 3-bit KV causes significant
  degradation, 4-bit almost none on Qwen3-8B/LLaMA3-8B per one 2025 paper [R-26].
- Model-native low precision changes the reference: when a model is *published* in MXFP4/FP8
  (e.g., MoE releases), that published format, not a BF16 up-cast, is the quality reference (PK).
- **Policy implication:** a quantized variant may only be selected automatically if its measured
  KLD/flip/long-context deltas vs. the declared reference are stored as metadata and shown to the
  user; otherwise the system must default to the reference format plus lossless offload.

### F3. KV cache and serving techniques

| Technique | Quality class | Notes / evidence |
|---|---|---|
| PagedAttention / block KV | lossless | non-contiguous KV blocks, near-zero fragmentation [R-65]; vLLM default block 64 [R-05] |
| Continuous batching | lossless (not batch-invariant bitwise) | batch shape changes reduction order (PK) |
| Prefix caching / RadixAttention | lossless | radix-tree KV reuse [R-66]; FlashInfer cascade attention for shared prefixes [R-64] |
| Chunked prefill | lossless | splits long prefills to bound decode latency (PK) |
| KV offload to CPU/disk (vLLM native, LMCache) | lossless at same precision; configurable-lossy if compressed | LMCache tiers CPU/disk/remote, vLLM connector; authors claim up to 15× throughput on multi-round QA [R-31][R-32] |
| Speculative decoding (draft, EAGLE-3, MTP, n-gram) | lossless with correct rejection-sampling verification; greedy gives exact match | [R-29][R-27][R-28]; llama.cpp notes cross-backend sampling can differ even with fixed seed and recommends greedy for exact matching [R-30] |
| "Relaxed"/typical acceptance variants | lossy | trade acceptance for fidelity (PK) |
| KV quantization | lossy | see F2 |

Acceptance length ≠ speedup: drafting/verification overhead decides realized throughput [R-27].
Medusa was removed from vLLM in v0.33.0 [R-05], so EAGLE-3/MTP are the forward-looking paths.

### F4. Attention kernels

- **FlashAttention-2:** Ampere, Ada, Hopper (e.g., RTX 4090); FP16/BF16; head dim ≤256 [R-18].
  Exact attention → lossless.
- **FlashAttention-3:** Hopper-only (beta), FP16/BF16 fwd/bwd, FP8 fwd [R-18][R-67]. FP8 path lossy.
- **FlashAttention-4 (CuTe DSL):** Hopper and Blackwell (H100, B200); no sm_120 mention [R-18].
  → **On Ada the Dao-AILab line stops at FA2.**
- **AMD:** CK backend supports MI200–MI355 and RDNA3/4 (ROCm ≥6.0); Triton backend (aiter
  kernels) supports CDNA and RDNA [R-18].
- **FlashInfer:** sm_75 through sm_120/121 incl. sm_89; paged/ragged KV, cascade attention,
  sampling kernels, FP8 GEMM; FP4 GEMM Blackwell-only; used by vLLM, SGLang, TRT-LLM, MLC-LLM [R-64].
- **SageAttention 1/2/2++/3:** INT8 QKᵀ, FP8 PV (2++), FP4 microscaling (3, Blackwell); Ampere/Ada/
  Hopper kernels; claims "without losing accuracy"; README itself recommends SA2 for
  precision-sensitive work; kernel-only 560 TOPS on RTX 5090 (2.7× FA2, excludes quantization
  overhead) [R-19]. **Classify as lossy** (quantized attention), opt-in only.

### F5. Compilers, graphs and kernel DSLs

- **CUDA Graphs** remove per-kernel launch overhead in decode; lossless; used by vLLM/LMDeploy
  (LMDeploy claims 1.3× on Llama3-8B for its PyTorch engine) [R-56].
- **torch.compile / Inductor** generates Triton GPU kernels (PK); lossless in intent but fusion and
  reduced-precision reductions can change numerics (PK) → treat as "lossless, verify by KLD".
- **Triton** is the de-facto portable kernel layer (NVIDIA, AMD; Intel via XPU backend — PK);
  HipKittens authors report Triton BF16 GEMM up to 3× slower than their AMD tile kernels on
  MI325X/MI355X (authors' claim; CDNA only) [R-48].
- **CUTLASS / CuTe DSL:** FA4 is written in CuTe DSL [R-18]; sm_120 is a separate target, and
  sm100a kernels do not run on GeForce Blackwell [R-20] — vendor-*generation* portability is a
  real problem even within NVIDIA.
- **ThunderKittens → HipKittens:** tile DSL ported to AMD with AMD engineers; productionized in
  AITER per MLSys 2026 [R-48].
- **TVM/MLC:** the only mature engine compiling one model to CUDA, ROCm, Vulkan, Metal and WebGPU
  [R-55]. **Mojo/MAX:** single stack for NVIDIA+AMD incl. RDNA since 26.2 [R-17].
- **IREE, OpenXLA, MLIR, tinygrad:** not verified this session (PK: IREE targets Vulkan/CUDA/ROCm/
  CPU; tinygrad has its own AMD/NVIDIA drivers). Treat as watch-list, not foundation.
- **Vulkan compute as fallback:** llama.cpp community benchmarks show RTX 4090 Vulkan (coopmat2,
  FA on, Llama-2-7B Q4_0, commit 4ae88d0) at pp512 ≈10.8k t/s and tg128 ≈190 t/s; no same-build
  CUDA number on that page; an older RTX 3080 comparison showed CUDA ~2.6× faster in pp and ~2×
  in tg [R-49]. Vulkan FA historically depended on coopmat2/new NVIDIA drivers [R-49].

### F6. Non-LLM workloads

**Image generation.** ComfyUI supports NVIDIA, ROCm, Intel Arc, Apple, Ascend and others, with
"smart" VRAM/RAM management and async weight streaming (claimed to run large models on 4 GB
VRAM + 8 GB RAM) [R-58]. stable-diffusion.cpp (MIT, ggml) covers SD1.x–3.5, FLUX.1/2, Qwen-Image,
Z-Image and video (Wan2.x, LTX-2.x, HunyuanVideo 1.5) with CUDA/Vulkan/Metal/OpenCL/SYCL/CPU and
GGUF weights [R-57]. Nunchaku/SVDQuant v1.2.0 (Jan 2026): W4A4 with 16-bit low-rank branch;
FLUX.1-dev ~3.6× smaller than BF16; INT4 ~3.0× faster than NF4 W4A16 on RTX 4090 (authors'
claims) [R-52][R-53] — lossy. TensorRT for diffusion: not verified this session.

**Step caching (TeaCache, First-Block-Cache, EasyCache).** All skip or reuse block/step
outputs based on a threshold → **lossy, configurable**. TeaCache authors claim up to 4.41× on
Open-Sora-Plan with −0.07 % VBench [R-43]; vLLM-Omni exposes `rel_l1_thresh` (default 0.2,
range 0.1–0.8) [R-45]; later work (PromptTea, ACID) reports degradation in complex scenes and
that one fixed threshold is hard to tune [R-44]. FBCache specifics: not verified.

**Video generation.** Wan2.2: A14B MoE (~27B total, ~14B active per step) stated to need ≥80 GB
on one GPU without offload; TI2V-5B runs 720p (1280×704, 24 fps) on ≥24 GB such as RTX 4090;
`--offload_model`, `--t5_cpu`, `--convert_model_dtype` reduce VRAM; "5-second 720P video in under
9 minutes on a single consumer-grade GPU" (GPU unspecified) [R-59]. Offload is lossless;
`--convert_model_dtype` is configurable-lossy (depends on target dtype). ComfyUI and sd.cpp also
support LTX-Video 2.x and HunyuanVideo 1.5 [R-57][R-58].

**Audio.** whisper.cpp (MIT): CUDA, Vulkan, HIP, MUSA, OpenVINO encoder, CANN, Core ML, and an
AMD Ryzen AI NPU (VitisAI) encoder path on Windows and Linux; integer quantization (lossy); VAD;
OpenAI-like server [R-62]. faster-whisper (CTranslate2, MIT): int8/fp16; large-v2 on RTX 3070 Ti,
13-min audio, fp16, beam 5: 1m03s vs. 2m23s for openai/whisper (project's claim) [R-61].
Piper was archived on 6 Oct 2025; development moved to OHF-Voice/piper1-gpl (license
presumably GPL, unverified) [R-63] — a licensing consideration for the base image. NVIDIA Parakeet,
Kokoro and other TTS engines: not verified this session.

**Embeddings / reranking.** TEI (Apache-2.0): Ada image (`89-*`), Ampere/Hopper/Blackwell, CPU
(ONNX or MKL), Metal, ROCm MI200/MI300 only; token-based dynamic batching; embeddings, rerankers,
SPLADE [R-60]. Consumer AMD/Intel GPUs are not covered by TEI → llama.cpp embeddings or
ONNX Runtime/OpenVINO are the portable path (PK for llama.cpp embeddings; Infinity not verified).

**Autonomous agents.** Runtime needs: many concurrent short calls with long shared prefixes
(system prompt, tool schemas) → prefix caching is the single most valuable lossless feature
[R-66][R-64]; constrained decoding for tool calls (PK); tool execution sandboxing and MCP servers
(MCP spec, PK [R-72]) belong to the security architecture, not the inference engine. Agents also
generate repetitive text (code edits) where draft-less n-gram speculation is cheap [R-30].

**Training / fine-tuning on 24 GB.** QLoRA (4-bit frozen base + LoRA adapters) is the standard
24 GB path [R-71]. Unsloth (Apache-2.0 core, AGPL-3.0 Studio UI) supports LoRA/QLoRA/full FT,
GRPO/DPO, FP8 training, NVIDIA/AMD/Intel; claims "2× faster, 70 % less VRAM, no accuracy loss"
without stated baseline on the README [R-40]. torchtune "no longer actively maintained" [R-39].
KTransformers SFT reports Qwen3-30B-A3B LoRA on 1× RTX 4090 (no batch/seq given) [R-54].
Axolotl, TRL, PEFT, Liger kernels, FSDP2, DeepSpeed: not verified this session (PK: all active;
Liger fuses kernels with exact math). QLoRA is **configurable-lossy** relative to BF16 LoRA.

### F7. Multi-vendor backends and NPUs

- **CUDA:** reference path; vLLM requires CUDA 13.0 from v0.33 [R-05] (PK: implies R580+
  driver) — a packaging constraint for the distro's driver channel.
- **ROCm/HIP:** ROCm became TheRock-built from 7.14 [R-34][R-35]; ROCm 10.0 docs list RDNA3
  (gfx110X: RX 7900 XT/XTX/GRE) and RDNA4 (gfx120X: RX 9070/9060) targets [R-33]; vLLM ships
  ROCm 10.0 wheels [R-05]. Community reports show gaps for some APUs (e.g., FA on gfx1151 builds)
  [R-34]. ONNX Runtime removed its ROCm EP (since 1.23); MIGraphX EP continues as plugin [R-47].
- **Intel:** IPEX EOL after Mar 2026 and ipex-llm archived 28 Jan 2026; Intel recommends upstream
  PyTorch XPU [R-46]. vLLM XPU wheels [R-05], SGLang XPU (secondary) [R-16], llama.cpp SYCL and
  OpenVINO backends [R-02]. OpenVINO 2026.x; linux-npu-driver v1.35.0 (Jul 2026, packaged by
  openSUSE [R-51]), v1.39.1 pre-release (Nov 2026, Nova Lake) [R-50][R-80].
- **Vulkan:** the only GPU API with first-class support in llama.cpp, whisper.cpp, sd.cpp and
  MLC-LLM simultaneously [R-02][R-55][R-57][R-62] → universal fallback.
- **ZLUDA:** CUDA 13.1 compatibility (Jan 2026), llama.cpp "nearly identical" to native ROCm per
  developers, v6 (Jun 2026) lost commercial funding → hobby status [R-36]. Legal/maintenance risk.
- **AMD XDNA NPU:** Lemonade 10.0 (Mar 2026) brought Linux NPU LLM + Whisper; FastFlowLM 1.0
  (Aug 2026) joined the ROCm org, MIT, XDNA2+ only; needs Linux 7.0 or amdxdna backports
  (contradicted, see below) [R-37][R-38]; Unsloth Desktop added Linux NPU inference (Nov 2026)
  [R-79]. IRON/MLIR-AIE: not verified.
- **ONNX Runtime EPs:** CUDA/TensorRT, MIGraphX, OpenVINO, (ROCm removed) [R-47] — good for
  embeddings/ASR/vision, weak for LLM serving (PK).
- **WebGPU:** llama.cpp (Dawn) and MLC/WebLLM [R-02][R-55] — relevant only for sandboxed UIs.

**Candidate portable abstraction layers:** (1) ggml (llama.cpp/whisper.cpp/sd.cpp share it →
one backend matrix covers LLM, ASR and diffusion) [R-02][R-57][R-62]; (2) PyTorch + Triton (vLLM,
SGLang, diffusers, ComfyUI, training) with CUDA/ROCm/XPU wheels [R-05][R-46]; (3) ONNX Runtime/
OpenVINO for NPUs and small models [R-47][R-50]. **Pluggable engines:** llama.cpp (default),
vLLM and SGLang (batched/agents), ExLlamaV3 and TensorRT-LLM (optional NVIDIA fast paths),
FastFlowLM/OpenVINO GenAI (NPU), KTransformers (huge MoE).

### F8. Sharing one GPU among concurrent workloads

- **Time slicing** is the default on GeForce; contexts are isolated but share VRAM with no quota
  (PK). **MIG** exists only on data-center parts (PK) — not on RTX 4090.
- **MPS:** Linux/QNX only; GeForce support not stated in the docs seen; a forum user infers it from
  compute capability, Folding@home users report it on RTX 3090 [R-41][R-42]. MPS weakens isolation:
  out-of-range writes of one client can corrupt another without error [R-41] → only for processes of
  the same trust domain; never as a silent default (security principle).
- **VRAM contention:** vLLM pre-sizes its KV cache from free memory after warmup [R-05], so a later
  ComfyUI job can OOM; vLLM sleep mode (level 1: weights → CPU RAM, KV dropped; level 2: discard)
  works on CUDA, ROCm, XPU but its HTTP endpoints are dev-mode only [R-06]. ComfyUI offloads and
  streams weights [R-58]. PyTorch's caching allocator fragmentation and `expandable_segments`:
  PK, not verified.
- **Stream priorities** only help inside one process (PK); cross-process QoS needs a broker.

## Optimization opportunities

| ID | Level | Opportunity | Expected benefit | Feasibility | Risk | Effort | Quality impact | Evidence | Novelty |
|---|---|---|---|---|---|---|---|---|---|
| O-R-01 | runtime | Engine abstraction: llama.cpp default + vLLM/SGLang + optional NVIDIA engines + NPU engines behind one OpenAI-compatible gateway with capability descriptors | H: right engine per workload/vendor | High (all expose OpenAI APIs) | API drift, feature mismatch | L | lossless | [R-01][R-05][R-13][R-37] | known-elsewhere |
| O-R-02 | platform | Quality gate service: KLD vs. reference logits, flip rate, long-context NIAH/RULER; results stored as model metadata; lossy variants need explicit opt-in | H: enforces policy, catches kernel bugs | High (llama-perplexity, lm-eval PK) | compute cost (37 GiB logits) | M | lossless (measurement) | [R-07][R-23][R-24][R-25] | novel (as OS service) |
| O-R-03 | runtime | Prefix caching on by default for agent/tool workloads | H for agents: skips repeated prefill | High | memory pressure, eviction tuning | S | lossless | [R-64][R-66] | established |
| O-R-04 | runtime | Speculative decoding presets: n-gram first, EAGLE-3/MTP when draft available, greedy-exact test mode | M–H decode latency on repetitive text | High (llama.cpp, vLLM) | slowdown on low acceptance | M | lossless | [R-27][R-29][R-30] | established |
| O-R-05 | runtime | MoE expert placement (hot on GPU, cold on CPU AMX/AVX) instead of lower-bit quants | H: run larger MoE on 24 GB at reference precision | Medium | RAM bandwidth bound, NUMA | M | lossless at same precision | [R-54] | known-elsewhere |
| O-R-06 | userspace | VRAM broker: per-workload budgets, sleep/unload/offload hooks (vLLM sleep, llama.cpp unload, ComfyUI offload) | H: LLM + diffusion co-existence without OOM | Medium | switch latency, engine APIs dev-only | L | lossless | [R-05][R-06][R-58] | novel (cross-engine) |
| O-R-07 | runtime | Auto backend selection per GPU (CUDA/HIP/SYCL/Vulkan) via llama-bench on install | M: avoids slow defaults (e.g., ROCm vs Vulkan on RDNA) | High | benchmark noise | S | lossless | [R-02][R-49][R-74] | known-elsewhere |
| O-R-08 | runtime | Opt-in KV-cache FP8/q8_0 with long-context eval disclosure | M: 2× context or more concurrency | High | long-context regressions | S | lossy | [R-07][R-25][R-26] | established |
| O-R-09 | application | Diffusion presets: "reference" (offload, FA2/SDPA) vs. labeled "fast" (SageAttention, Nunchaku, TeaCache thresholds) | M–H speed for image/video | High | user confusion | M | configurable-lossy | [R-19][R-43][R-52] | known-elsewhere |
| O-R-10 | runtime | KV offload tier (vLLM CPU offload / LMCache) for long agent sessions and multi-turn chat | M: avoid recompute after eviction | Medium | RAM use, version coupling | M | lossless (uncompressed) | [R-31][R-32] | established |
| O-R-11 | runtime | Ship CUDA-Graph + compiled paths enabled and validated per release | M decode latency | High | numeric drift | S | lossless (verify KLD) | [R-56] | established |
| O-R-12 | userspace | NPU offload of always-on small models (ASR encoder, embeddings, wake/VAD, small LLM) | M: frees GPU, lower power | Medium (driver maturity) | XDNA2-only, kernel version | M | configurable-lossy (NPU formats) | [R-37][R-50][R-62] | known-elsewhere |
| O-R-13 | platform | Verified quant catalog: per-model variants with measured KLD/flip tables and hardware-fit hints | M: informed choice | High | storage, eval cost | M | lossy variants, labeled | [R-11][R-23][R-24] | known-elsewhere |
| O-R-14 | runtime | Determinism / batch-invariant mode for reproducible evals and regression tests | M for trust & testing | Medium | slower kernels | M | lossless | PK | known-elsewhere |
| O-R-15 | application | Fine-tuning profile for 24 GB (QLoRA via Unsloth/TRL+PEFT, MoE via KTransformers) | M: local adaptation | High | dependency churn | M | configurable-lossy | [R-40][R-54][R-71] | established |
| O-R-16 | userspace | MPS as opt-in for trusted same-user multi-process serving | L–M concurrency | Low–Medium (GeForce unclear) | isolation loss | S | lossless | [R-41][R-42] | known-elsewhere |

## Hypotheses

**H-R-01 — llama.cpp is close enough on NVIDIA single-user decode.**
Statement: at batch 1, dense models ≤32B at 4–5 bpw, llama.cpp CUDA decode on RTX 4090 is within
15 % of the fastest NVIDIA-specific engine (ExLlamaV3/TRT-LLM). Rationale: decode is memory-bandwidth
bound; all engines read similar bytes per token. Test: llama-bench vs. ExLlamaV3/TRT-LLM at matched
bpw and measured KLD. Risk: if false, an NVIDIA fast-path engine becomes mandatory.

**H-R-02 — Vulkan is a viable universal fallback for decode, not prefill.**
Statement: llama.cpp Vulkan reaches ≥85 % of CUDA tg128 on RTX 4090 but materially less pp512.
Rationale: [R-49] and secondary reports [R-85] show smaller tg than pp gaps. Test: same commit,
both backends, three models. Risk: fallback UX worse on long-prompt (RAG/agent) workloads.

**H-R-03 — KLD + flips suffice for weight quant gating, not for KV quant.**
Statement: KLD/flips on short contexts predict task regressions for weight quantization, but
KV-cache quantization requires ≥32k-token retrieval tests. Rationale: [R-24][R-25][R-07]. Test:
correlate KLD with NIAH/RULER deltas for KV fp8/q8_0/q4_0 on 3 models. Risk: gate passes harmful configs.

**H-R-04 — Lossless speculation pays for agents/coding, not for high-temperature chat.**
Rationale: acceptance depends on repetition and temperature [R-27][R-30]. Test: SPEED-Bench-style
runs [R-30] across coding, agent traces, creative prompts at T∈{0, 0.7, 1.0}. Risk: default-on
speculation slows some users.

**H-R-05 — A VRAM broker enables LLM + diffusion co-residence on 24 GB.**
Statement: with sleep/offload hooks, switching between a 14B LLM and a FLUX/Wan job costs only a few
seconds of PCIe transfer and avoids OOMs. Rationale: [R-06][R-58]. Test: scripted mixed workload,
measure OOM count and switch latency. Risk: engine APIs (dev-mode endpoints) unstable.

**H-R-06 — FP4 models on Ada gain nothing over INT4 W4A16.**
Statement: NVFP4/MXFP4 on sm_89 runs via dequantization with throughput ≤ Marlin-class INT4.
Rationale: no FP4 tensor cores on Ada (PK) [R-20]. Test: same model in MXFP4 vs. AWQ on RTX 4090.
Risk: misleading "FP4 support" labels.

**H-R-07 — On consumer AMD, ROCm 10 wins for batched serving, Vulkan for single-user.**
Rationale: vLLM/SGLang need ROCm; [R-74] reports Vulkan ahead in llama.cpp. Test: RX 7900 XTX and
RX 9070 XT, llama.cpp HIP vs Vulkan, vLLM ROCm. Risk: wrong default backend on AMD.

**H-R-08 — Bitwise non-determinism is not quality loss, but breaks naive regression tests.**
Statement: continuous batching yields token divergence between runs at T=0 without changing
KLD-measured quality. Test: repeated greedy runs at varying concurrency; compare tokens and KLD.
Risk: false alarms in the quality gate unless tolerance-based.

**H-R-09 — MPS works on RTX 4090 under Linux and improves concurrent small-batch throughput.**
Rationale: [R-42]. Test: start MPS daemon, run two engines, measure throughput and fault isolation.
Risk: isolation loss; unsupported configuration.

## Contradictory or weak evidence

- **Ollama engine:** v0.17 "own engine replacing llama.cpp server mode" [R-14] vs. v0.30 "GGUF via
  llama.cpp" [R-13]; both may hold (format-dependent engines). Release dates differ across sources.
- **FP4 on consumer Blackwell:** marketing/press claims "same 5th-gen tensor cores" vs. CUTLASS
  forum/wiki: SM12x lacks tcgen05, uses block-scaled mma.sync; MXFP4 build/selection bugs on SM120
  [R-20][R-21][R-22].
- **AMD NPU kernel requirement:** Linux 7.0 or backports [R-37] vs. "kernel ≥6.14 has amdxdna" [R-38].
- **ROCm 10.0:** one search returned no ROCm 10 release while ROCm docs and vLLM releases reference it
  [R-05][R-33]; ROCm 10 docs were seen only as search snippets (host blocked).
- **Vulkan vs CUDA gap:** older RTX 3080 data shows CUDA ~2–2.6× faster [R-49]; newer forum claims
  "tg roughly the same, pp lower" [R-85]. No same-build RTX 4090 comparison found.
- **SageAttention:** "no accuracy loss" claim vs. README advising SA2 over SA3 for precision [R-19].
- **TeaCache:** authors' −0.07 % VBench [R-43] vs. degradation reported by PromptTea/ACID [R-44];
  one paper even mis-describes TeaCache as a learned policy (likely wrong).
- **Nunchaku** reports both 8.7× and 10.1× speedups for laptop 4090 without reconciliation [R-52].
- **TGI archival** (Mar 2026) only from a third party [R-81]; official docs say maintenance mode [R-08].
- **ONNX Runtime ROCm EP:** "removed since 1.23" vs. still listed as "deprecated" [R-47].
- **IPEX EOL timing:** March 2026 (Intel docs) vs. June 2026 timeline (press) [R-46].
- **llama.cpp v1.0 details** (FP8 KV cache, removed functions, authorship) come from secondary
  sources that disagree on authors [R-03].
- **Unsloth / LMDeploy / KTransformers performance claims** lack baselines on their READMEs [R-40][R-54][R-56].

## Open questions

1. Same-build RTX 4090 comparison: llama.cpp CUDA vs. Vulkan vs. ExLlamaV3 vs. vLLM at batch 1 and
   batch 8–32, with KLD-matched quants (feeds H-R-01/02).
2. Does SGLang (or V1.0) officially support consumer RDNA3/RDNA4 and Intel Arc? Does vLLM's ROCm 10
   build list gfx110X/gfx120X as supported or experimental?
3. Which engines produce batch-invariant (deterministic) output, and at what cost?
4. Is MPS supported/stable on GeForce with current drivers, and how does it interact with
   CUDA 13 / R580+ drivers?
5. What is the minimum kernel/firmware/userspace stack for XDNA2 and Intel NPU on our target distro,
   and do NPU paths require lossy formats for every model class?
6. Which quality-eval suite (KLD corpus, flip benchmarks, NIAH/RULER, image/video metrics) is cheap
   enough to run per model variant on a 24 GB machine?
7. For diffusion step caching, can thresholds be chosen per prompt adaptively with bounded quality
   loss (ACID-style), and how do we label that to users?
8. Status of ExLlamaV2, Infinity (embeddings), Parakeet/Kokoro runtimes on Linux non-NVIDIA,
   IREE and tinygrad — not verified this session.
9. Licensing of components in a base image: Piper successor (GPL?), Unsloth Studio (AGPL),
   Modular Community License — to be reconciled with 05-base-distributions-and-licensing.md.

## Access limitations

- **DNS-blocked hosts (WebFetch failed, not retried):** `huggingface.co`, `ollama.com`, `vllm.ai`,
  `phoronix.com`, `rocm.docs.amd.com`, `docs.nvidia.com`. Content from these hosts was seen only as
  search-result summaries (marked "search snippet" in the sources table).
- **github.com** `blob/` page for llama.cpp `docs/speculative.md` returned HTTP 503; the same file was
  read via `raw.githubusercontent.com`.
- **Web search budget exhausted** (shared per-turn limit) after ~27 searches; planned searches on
  ComfyUI memory management, Parakeet/Kokoro, Infinity, Axolotl/TRL/PEFT/Liger, IREE/XLA/tinygrad,
  HQQ/bitsandbytes, TensorRT diffusion and FBCache were not performed. Related statements are marked
  PK or "not verified".
- Papers cited by arXiv ID from prior knowledge ([R-12], [R-28], [R-29], [R-53], [R-65]–[R-71]) were not
  fetched this session (arxiv.org not tested to save budget); their IDs and claims should be re-checked.
- No hands-on benchmark was possible; all numbers are third-party claims.

## Sources

| ID | Title | URL | Date | Accessed | Grade | Notes |
|---|---|---|---|---|---|---|
| R-01 | llama.cpp repository README | https://github.com/ggml-org/llama.cpp | n.d. (2026-11) | 2026-11-26 | B | MIT; `llama serve`; v1.0.0 link |
| R-02 | llama.cpp build guide (docs/build.md) | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/build.md | n.d. | 2026-11-26 | B | backend list, HIP notes |
| R-03 | llama.cpp v1.0.0 coverage (neurohive, ai-tldr, letsdatascience) | https://neurohive.io/en/news/llama-cpp-v1-stable-release-semver/ | 2026-11-13 | 2026-11-26 (search snippet) | C | HF blog itself blocked; authorship disagrees |
| R-04 | Simon Willison: ggml.ai joins Hugging Face | https://simonwillison.net/2026/Feb/20/ggmlai-joins-hugging-face/ | 2026-02-20 | 2026-11-26 (search snippet) | C | title only |
| R-05 | vLLM GitHub releases (v0.32.0–v0.34.0) | https://github.com/vllm-project/vllm/releases | 2026-10-18 – 2026-11-16 | 2026-11-26 | B | CUDA 13, ROCm 10, MRv2, NVFP4 KV |
| R-06 | vLLM docs: sleep mode | https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/sleep_mode.md | n.d. | 2026-11-26 | B | CUDA/ROCm/XPU; dev-mode endpoints |
| R-07 | vLLM blog: State of FP8 KV-cache and attention quantization | https://vllm.ai/blog/2026-04-22-fp8-kvcache | 2026-04-22 | 2026-11-26 (search snippet) | B | NIAH 91 %→13 % before fix |
| R-08 | TGI docs/README (maintenance mode) | https://huggingface.co/docs/text-generation-inference/index | n.d. | 2026-11-26 (search snippet) | B | recommends vLLM/SGLang |
| R-09 | TensorRT-LLM support matrix | https://nvidia.github.io/TensorRT-LLM/reference/support-matrix.html | n.d. (1.3.0rc) | 2026-11-26 (search snippet) | A | SM89 FP8; SM100/120 FP4 |
| R-10 | tensorrt-llm PyPI mirror | https://simple-repository.app.cern.ch/project/tensorrt-llm | 2026-04-20 | 2026-11-26 (search snippet) | C | 1.2.1 |
| R-11 | ExLlamaV3 repository | https://github.com/turboderp-org/exllamav3 | n.d. | 2026-11-26 | B | QTIP variant; CUDA ≥12.4; MIT |
| R-12 | Tseng et al., QTIP (arXiv 2406.11235) | https://arxiv.org/abs/2406.11235 | 2024 | not accessed (PK) | B | trellis quantization |
| R-13 | Ollama blog: Improved performance and model support with GGUF | https://ollama.com/blog/improved-performance-and-model-support-with-gguf | ~2026-06-05 | 2026-11-26 (search snippet) | B | v0.30 llama.cpp + MLX |
| R-14 | WebProNews: Ollama 0.17 new engine | https://www.webpronews.com/ollama-0-17-arrives-with-massive-performance-gains-and-a-new-architecture-that-could-reshape-local-ai-deployment/ | 2026 | 2026-11-26 (search snippet) | D | press, unverified claims |
| R-15 | LMSYS: SGLang summit recap / consortium | https://www.lmsys.org/blog/2026-11-17-sglang-summit-recap | 2026-11-17 | 2026-11-26 (search snippet) | B | V1.0 slide |
| R-16 | SGLang v0.5.20 coverage (Morph, Digg) | https://www.morphllm.com/comparisons/sglang-vs-vllm | 2026-09 | 2026-11-26 (search snippet) | C | XPU in standard releases |
| R-17 | Modular 26.2 blog + MAX changelog | https://modular.com/blog/modular-26-2-state-of-the-art-image-generation-and-upgraded-ai-coding-with-mojo | 2026-03-19 | 2026-11-26 (search snippet) | B | RDNA consumer; license split |
| R-18 | FlashAttention repository | https://github.com/Dao-AILab/flash-attention | n.d. | 2026-11-26 | B | FA2/3/4 arch support; ROCm CK/Triton |
| R-19 | SageAttention repository | https://github.com/thu-ml/SageAttention | 2025-09-27 (latest news) | 2026-11-26 | B | INT8/FP8/FP4 attention |
| R-20 | CUTLASS Blackwell targets + NVIDIA forum on SM12x tcgen05 | https://forums.developer.nvidia.com/t/dearest-cutlass-team-when-the-hell-are-you-going-to-properly-fix-tcgen05-fp4-support-for-dgx-spark-gb10-sm121/359598 | 2026 | 2026-11-26 (search snippet) | D | sm100a ≠ sm120 |
| R-21 | Blackwell GPU wiki: NVFP4 deep dive | https://0xsero.github.io/blackwell-gpu-wiki/blackwell/nvfp4-deep-dive/ | n.d. | 2026-11-26 (search snippet) | D | mma.sync block-scale path |
| R-22 | NVIDIA blog: Introducing NVFP4; vLLM issue #31085; llama.cpp issue #19662 | https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference | 2025–2026 | 2026-11-26 (search snippet) | C | FP4 format; SM120 MXFP4 bugs |
| R-23 | llama.cpp tools/perplexity README (KLD) | https://raw.githubusercontent.com/ggml-org/llama.cpp/HEAD/tools/perplexity/README.md | n.d. | 2026-11-26 (search snippet) | B | KLD workflow, LLaMA-3 table |
| R-24 | Dutta et al., Accuracy is Not All You Need (NeurIPS 2024) | https://arxiv.org/abs/2407.09141 | 2024 | 2026-11-26 (search snippet) | A | flips, KLD correlation |
| R-25 | Evaluating Quantized LLMs (arXiv 2402.18158) | https://arxiv.org/pdf/2402.18158 | 2024 | 2026-11-26 (search snippet) | B | KV8/KV4 long-text sensitivity |
| R-26 | Kitty: 2-bit KV cache quantization (arXiv 2511.18643) | https://arxiv.org/pdf/2511.18643 | 2025 | 2026-11-26 (search snippet) | B | 3-bit degrades, 4-bit ~lossless |
| R-27 | vLLM/AMD blog: EAGLE-3 on AMD Instinct | https://vllm.ai/blog/2026-07-13-eagle-3-amd-instinct | 2026-07-13 | 2026-11-26 (search snippet) | B | lossless verification; acceptance ≠ speedup |
| R-28 | Li et al., EAGLE-3 (arXiv 2503.01840) | https://arxiv.org/abs/2503.01840 | 2025 | not accessed (PK) | B | draft from target hidden states |
| R-29 | Leviathan et al., Fast Inference via Speculative Decoding (ICML 2023) | https://arxiv.org/abs/2211.17192 | 2023 | not accessed (PK) | A | rejection sampling preserves distribution |
| R-30 | llama.cpp docs/speculative.md | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/speculative.md | n.d. | 2026-11-26 | B | draft, EAGLE-3, MTP, DFlash, n-gram |
| R-31 | LMCache paper (arXiv 2510.09665) | https://arxiv.org/abs/2510.09665 | 2025 | 2026-11-26 (search snippet) | B | tiers; up to 15× (authors) |
| R-32 | NVIDIA Dynamo docs: KV cache offloading (vLLM) | https://docs.nvidia.com/dynamo/knowledge-base/modular-components/backends/v-llm/kv-cache-offloading | n.d. | 2026-11-26 (search snippet) | A | LMCache via connector |
| R-33 | ROCm 10.0.0 release notes / transition guide | https://rocm.docs.amd.com/en/docs-10.0.0/about/transition-guide-TheRock.html | 2026 | 2026-11-26 (search snippet) | A | gfx110X, gfx120X listed |
| R-34 | AMD blog: ROCm 7.14 TheRock production; Framework community notes | https://rocm.blogs.amd.com/ecosystems-and-partners/rocm-7.14-blog/README.html | 2026 | 2026-11-26 (search snippet) | B | TheRock; gfx1151 gaps (community) |
| R-35 | ROCm/TheRock repository | https://github.com/ROCm/TheRock | n.d. | 2026-11-26 | B | "since 7.14 built via TheRock" |
| R-36 | ZLUDA coverage (Phoronix Q4-2025 report, v6; Tom's Hardware) | https://phoronix.com/news/ZLUDA-6-Released | 2026-01 – 2026-09 | 2026-11-26 (search snippet) | C | hobby status, CUDA 13.1 |
| R-37 | Phoronix: Lemonade 10.0/10.0.1, FastFlowLM 1.0 | https://phoronix.com/news/FastFlowLM-1.0 | 2026-03 – 2026-08 | 2026-11-26 (search snippet) | C | XDNA2, ROCm org, MIT |
| R-38 | nix-amd-ai wiki (amdxdna on ≥6.14) | https://github.com/noamsto/nix-amd-ai/wiki | n.d. | 2026-11-26 (search snippet) | D | contradicts kernel 7.0 claim |
| R-39 | torchtune README | https://raw.githubusercontent.com/meta-pytorch/torchtune/main/README.md | n.d. | 2026-11-26 (search snippet) | B | not actively maintained |
| R-40 | Unsloth repository | https://github.com/unslothai/unsloth | n.d. | 2026-11-26 | B | claims w/o baseline; AGPL Studio |
| R-41 | NVIDIA MPS documentation | https://docs.nvidia.com/deploy/mps/latest/when-to-use-mps.html | n.d. | 2026-11-26 (search snippet) | A | Linux/QNX only; isolation caveat |
| R-42 | NVIDIA forum (MPS on GeForce) + Folding@home MPS guide | https://forums.developer.nvidia.com/t/availability-of-mps-on-geforce-rtx-gpus/336479 | 2025–2026 | 2026-11-26 (search snippet) | D | anecdotal GeForce use |
| R-43 | Liu et al., TeaCache (CVPR 2025, arXiv 2411.19108) | https://arxiv.org/abs/2411.19108 | 2024/2025 | 2026-11-26 (search snippet) | A | 4.41×, −0.07 % VBench |
| R-44 | PromptTea (arXiv 2507.06739); ACID (arXiv 2607.12358) | https://arxiv.org/html/2507.06739v1 | 2025–2026 | 2026-11-26 (search snippet) | B | step-cache degradation |
| R-45 | vLLM-Omni docs: TeaCache | https://docs.vllm.ai/projects/vllm-omni/en/stable/user_guide/diffusion/cache_acceleration/teacache/ | n.d. | 2026-11-26 (search snippet) | B | threshold 0.1–0.8, default 0.2 |
| R-46 | Intel IPEX EOL notice; ipex-llm archived | https://github.com/intel/ipex-llm | 2026-01-28 | 2026-11-26 (search snippet) | B | use upstream PyTorch XPU |
| R-47 | ONNX Runtime EP docs; ROCm EP removal commit; AMD MIGraphX docs | https://onnxruntime.ai/docs/execution-providers/ | n.d. | 2026-11-26 (search snippet) | A | ROCm EP removed ≥1.23 |
| R-48 | HipKittens (arXiv 2511.08083; MLSys 2026 oral) | https://arxiv.org/abs/2511.08083 | 2025-11 | 2026-11-26 (search snippet) | B | CDNA only; vs Triton |
| R-49 | llama.cpp discussion #10879: Performance with Vulkan | https://github.com/ggml-org/llama.cpp/discussions/10879 | n.d. (rolling) | 2026-11-26 | C | community llama-bench data |
| R-50 | OpenVINO release notes / GenAI on NPU; intel/linux-npu-driver releases | https://github.com/intel/linux-npu-driver/releases | 2026 | 2026-11-26 (search snippet) | A | v1.35.0 Jul 2026 |
| R-51 | openSUSE: NPU driver 1.35.0 and OpenVINO 2026.3.1 | https://news.opensuse.org/2026/08/31/NPU-openVINO/ | 2026-08-31 | 2026-11-26 (search snippet) | C | distro packaging precedent |
| R-52 | Nunchaku repository | https://github.com/nunchaku-tech/nunchaku | 2026-01-12 (v1.2.0) | 2026-11-26 | B | SVDQuant engine; Apache-2.0 |
| R-53 | Li et al., SVDQuant (arXiv 2411.05007) | https://arxiv.org/abs/2411.05007 | 2024 | not accessed (PK) | B | W4A4 + low-rank |
| R-54 | KTransformers repository | https://github.com/kvcache-ai/ktransformers | 2026-10-15 (latest news) | 2026-11-26 | B | v0.7.0; hybrid MoE |
| R-55 | MLC-LLM repository | https://github.com/mlc-ai/mlc-llm | n.d. | 2026-11-26 | B | TVM; Vulkan/WebGPU |
| R-56 | LMDeploy repository | https://github.com/InternLM/lmdeploy | n.d. (v0.13.0) | 2026-11-26 | B | claims w/o context |
| R-57 | stable-diffusion.cpp repository | https://github.com/leejet/stable-diffusion.cpp | 2026-11-11 (latest news) | 2026-11-26 | B | ggml; video models; MIT |
| R-58 | ComfyUI README | https://raw.githubusercontent.com/comfyanonymous/ComfyUI/master/README.md | n.d. | 2026-11-26 | B | backends; weight streaming |
| R-59 | Wan2.2 repository | https://github.com/Wan-Video/Wan2.2 | 2025-07 – 2025-11 | 2026-11-26 | B | VRAM statements, offload flags |
| R-60 | text-embeddings-inference repository | https://github.com/huggingface/text-embeddings-inference | n.d. (1.10 tags) | 2026-11-26 | B | Ada image; ROCm MI200/300 |
| R-61 | faster-whisper repository | https://github.com/SYSTRAN/faster-whisper | n.d. | 2026-11-26 | B | CTranslate2; benchmark context |
| R-62 | whisper.cpp README | https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/README.md | n.d. | 2026-11-26 | B | VitisAI NPU encoder, Vulkan |
| R-63 | rhasspy/piper repository (archived) | https://github.com/rhasspy/piper | 2025-10-06 | 2026-11-26 | B | moved to OHF-Voice/piper1-gpl |
| R-64 | FlashInfer README | https://raw.githubusercontent.com/flashinfer-ai/flashinfer/main/README.md | n.d. | 2026-11-26 | B | sm_75–sm_121; cascade attention |
| R-65 | Kwon et al., PagedAttention / vLLM (SOSP 2023, arXiv 2309.06180) | https://arxiv.org/abs/2309.06180 | 2023 | not accessed (PK) | A | paged KV |
| R-66 | Zheng et al., SGLang / RadixAttention (NeurIPS 2024, arXiv 2312.07104) | https://arxiv.org/abs/2312.07104 | 2023/2024 | not accessed (PK) | A | radix-tree prefix reuse |
| R-67 | Shah et al., FlashAttention-3 (arXiv 2407.08608) | https://arxiv.org/abs/2407.08608 | 2024 | not accessed (PK) | B | Hopper-specific |
| R-68 | Lin et al., AWQ (MLSys 2024, arXiv 2306.00978) | https://arxiv.org/abs/2306.00978 | 2023/2024 | not accessed (PK) | A | activation-aware W4 |
| R-69 | Frantar et al., GPTQ (ICLR 2023, arXiv 2210.17323) | https://arxiv.org/abs/2210.17323 | 2022/2023 | not accessed (PK) | A | 2nd-order PTQ |
| R-70 | Mobius Labs, HQQ blog/repo | https://github.com/mobiusml/hqq | n.d. | not accessed (PK) | B | calibration-free |
| R-71 | Dettmers et al., QLoRA (NeurIPS 2023, arXiv 2305.14314) | https://arxiv.org/abs/2305.14314 | 2023 | not accessed (PK) | A | NF4 + LoRA |
| R-72 | Model Context Protocol specification | https://modelcontextprotocol.io/specification | n.d. | not accessed (PK) | A | agent tool protocol |
| R-74 | ROCm vs Vulkan guides/benchmarks (glukhov 2026; Phoronix Lemonade review) | https://glukhov.org/llm-hosting/comparisons/amd-rocm-vs-vulkan-llm-hosting/ | 2026 | 2026-11-26 (search snippet) | C | Vulkan > ROCm on some AMD |
| R-75 | WebProNews: Ollama Vulkan (0.12.11); webscraft Ollama 0.30 | https://www.webpronews.com/ollamas-vulkan-leap-unlocking-ai-power-on-non-nvidia-gpus | 2025–2026 | 2026-11-26 (search snippet) | D | press |
| R-79 | Phoronix: Unsloth Desktop supports Ryzen AI NPU on Linux | https://phoronix.com/news/Unsloth-Desktop-Ryzen-AI-NPU | 2026-11 | 2026-11-26 (search snippet) | C | via Lemonade/FastFlowLM |
| R-80 | linuxcompatible.org: Intel NPU driver 1.39.1 pre-release | https://www.linuxcompatible.org/story/intels-linux-npu-driver-1391-prerelease-targets-nova-lake-months-before-hardware-ships/ | 2026-11-19 | 2026-11-26 (search snippet) | C | mentions CVE-2026-20783 (unverified) |
| R-81 | glukhov: TGI install/config (archival claim) | https://glukhov.org/llm-hosting/tgi/ | 2026 | 2026-11-26 (search snippet) | C | archived 2026-03-21 (unverified) |
| R-85 | NVIDIA forum: Vulkan as alternative backend for llama.cpp; knightli 2026 scoreboard | https://forums.developer.nvidia.com/t/vulkan-as-alternative-backend-for-llama-cpp/363516 | 2026 | 2026-11-26 (search snippet) | D | "tg similar, pp lower" |
