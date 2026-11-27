# Target Hardware, Data Paths, Model Loading, Offloading and Distributed Compute

> Status: Phase 0 research · Last updated: 2026-11-26 · Evidence grading: see 00-method.md

## Summary

- **Decode is a bandwidth problem, and each tier is far slower than the one above it.** Theoretical peaks from datasheet numbers:
  RTX 4090 VRAM 1008 GB/s → DDR5-5200 dual-channel 83.2 GB/s → PCIe 4.0 x16 31.5 GB/s → PCIe 5.0 NVMe 14.8 GB/s → 1 GbE 0.125 GB/s
  [H-12][H-01][H-84][H-20]. Where the bytes read per token live sets the ceiling on tokens/s (see §3, all *theoretical*).
- **The 7950X3D is asymmetric.** One CCD has 96 MB of L3 and the other has 32 MB [H-03]. Each CCD reads from the I/O die at about 32 B per fabric clock
  (≈64 GB/s at FCLK 2 GHz) [H-08], which is less than DDR5 peak. CPU-side memory-bound work (offloaded layers or experts)
  probably has to span both CCDs. V-Cache cannot hold weights that are many GB (**H-H-01**).
- **2 DIMMs, not 4.** AMD's official speed drops from DDR5-5200 (2 DIMMs) to DDR5-3600 (4 DIMMs) [H-01][H-10]. That is −31 % theoretical
  bandwidth, and it directly hurts CPU-offloaded inference.
- **RTX 4090 limits that matter.** It has a PCIe 4.0 x16 host link and no NVLink [H-14]. NVIDIA says P2P is not supported on the 4090 [H-18].
  FP8 tensor math is available, but FP32-accumulate runs at half rate on GeForce, according to the revised whitepaper as reported [H-16].
  **GPUDirect Storage treats GeForce as compatibility-mode only** (support matrix: Data Center and Quadro only) [H-45].
- **GDS P2PDMA mode** removes the need for nvidia-fs. It needs the open kernel module (570+) [H-46]. NVIDIA lists a known
  failure when KASLR is enabled [H-45]. Disabling KASLR to get it would violate the project's security principle.
- **The cold-load floor is size divided by NVMe read rate.** For example, 20 GB takes ≥1.35 s *theoretical*. Loaders that read in parallel through
  pinned buffers (fastsafetensors, Model Streamer, ServerlessLLM) claim 4.8–10× over default safetensors loading in their
  own setups [H-42][H-44]. llama.cpp added O_DIRECT loading, then turned it off by default; mmap is again the default [H-36][H-37][H-35].
- **Offload policy.** For batch-1 decode, computing CPU-resident layers in place is bounded by DRAM (83 GB/s). That beats streaming those weights to the GPU
  over PCIe (31.5 GB/s), which is what vLLM `--cpu-offload-gb` does each forward pass [H-59] (**H-H-02**). MoE expert offload
  (`--cpu-moe`/`--n-cpu-moe`, KTransformers) is the highest-value technique for 24 GB VRAM + 64 GB RAM [H-35][H-57].
- **Distributing work across the two machines adds capacity, not speed.** The i5-11500 has 51.2 GB/s of DDR4 and 32 GB of RAM [H-22]. Its best roles are
  orchestrator and benchmark controller, energy logger, CI runner (AVX-512/VNNI and Gen12 iGPU coverage), CPU embeddings/ASR, and model cache.
  llama.cpp RPC is a proof of concept and insecure [H-41]. exo runs CPU-only on Linux [H-68].
- **Proxmox VE 9.2** (May 2026) runs kernel 7.0 [H-30]. The UI default CPU type `x86-64-v2-AES` [H-32] hides AVX2 and AVX-512 [H-86], so benchmarks must use
  `host`, with KSM and ballooning off. The Rocket Lake iGPU has no SR-IOV [H-24].
- **Energy telemetry is weak.** On A100/H100, nvidia-smi samples only ~25 % of runtime [H-71]. AMD RAPL showed accuracy gaps on Zen 2 [H-73],
  and no Zen 4 validation was found. RAPL has been root-only since PLATYPUS [H-76][H-74]. We need a privileged collector plus a wall meter for calibration.

## Scope

This document covers the first test hardware: (a) a workstation with a Ryzen 9 7950X3D, RTX 4090 24 GB, 64 GB DDR5 and a PCIe 5.0 NVMe drive, and (b) a server with an
i5-11500 and 32 GB, running Proxmox VE, with no discrete GPU. Topics: hardware facts; theoretical bandwidth bounds; the model-loading
path (formats, page cache, accelerated loaders, GDS, compression, dedup, file systems, checkpoint/restore); RAM/VRAM tiering
and offload; distributed and heterogeneous compute; Proxmox measurement caveats; energy measurement. Out of scope: runtime and kernel
internals (see 02/04) and benchmark methodology in general (see 06). **No measurements were taken.** Every number is either a
datasheet value, a derived *theoretical* bound, or a claim attributed to its source.

## Findings

### 1. Workstation hardware facts

**CPU: Ryzen 9 7950X3D.**
- **Topology.** 16 cores/32 threads on two Zen 4 CCDs plus one I/O die. Only one CCD carries the stacked 64 MB V-Cache: 32+64 = 96 MB L3 there, 32 MB on the
  other, 128 MB total [H-03][H-01]. Reviews report the V-Cache CCD clocks lower under load [H-03][H-04]. TDP 120 W [H-01]; TechPowerUp lists PPT 162 W [H-02].
- **Linux scheduling.** Since Linux 6.13 the `amd_3d_vcache` driver offers a sysfs switch between "frequency" mode (prefer the fast CCD) and "cache" mode
  (prefer the V-Cache CCD) [H-05]. No source confirmed which logical CPUs sit on the V-Cache CCD; read `/sys/devices/system/cpu/cpu*/cache/index3/size`.
- **Memory and PCIe.** Dual-channel DDR5, 128 GB max. Official speeds: 2x1R/2x2R **DDR5-5200**, 4x1R/4x2R **DDR5-3600** [H-01]. AMD has called DDR5-6000 the
  "sweet spot" [H-09], but that is an EXPO overclock above the official rating (stability risk). PCIe 5.0: 28 lanes total, 24 usable [H-01] (some databases say 24 [H-02]).
- **SIMD.** Zen 4 implements AVX-512 by **double-pumping 256-bit datapaths** [H-07]. `/proc/cpuinfo` on a 7950X lists `avx512_vnni` and `avx512_bf16` [H-06].
  There is no AMX, so AMX kernels such as KTransformers' AMX path [H-57] do not apply; the AVX-512 paths do.
- **Fabric.** Chips and Cheese puts the desktop Zen 4 CCD↔IOD link at **32 B/cycle read, 16 B/cycle write**: 64 GB/s read per CCD at FCLK 2 GHz, so one CCD
  "may not saturate" fast DDR5 [H-08].

**DDR5: 2 vs 4 DIMMs.** Moving from one DIMM per channel (1DPC) to 2DPC lowers the official JEDEC maximum to DDR5-3600 [H-10][H-01]. Each DDR5 DIMM carries 64 data bits
as two 32-bit subchannels [H-85], so peak dual-channel bandwidth is 16 B × MT/s. For 64 GB, use 2×32 GB at 1DPC; expand later with 2 larger DIMMs, not 4
(hypothesis; check the board QVL).

**GPU: RTX 4090.**
- **Chip, memory, link.** AD102 with 16,384 of 18,432 CUDA cores enabled [H-12]. 24 GB GDDR6X on a 384-bit bus at 21 Gbps gives **1008 GB/s** [H-12][H-13].
  450 W TGP [H-12][H-14]. PCIe 4.0 x16 host link [H-14], Resizable BAR supported [H-15].
- **Multi-GPU.** Puget lists multi-GPU technology (NVLink) as "Not Available" [H-14]. An NVIDIA representative stated "Peer to Peer is not supported on 4090" and
  that drivers should not report it [H-18][H-17]. A community fork of the open kernel modules (tinygrad, `565.57.01-p2p`) re-enables P2P [H-19]; it is
  unsupported and outside our principles for default use.
- **Tensor throughput.** 4th-gen tensor cores support FP8 [H-11][H-12]; Tom's lists 661 dense / 1321 sparse TFLOPS FP8 [H-12]. Forum reports say the whitepaper
  was revised so that **FP32-accumulate is half rate on GeForce**: FP16 tensor 330.3 TFLOPS with FP16 accumulate, 165.2 with FP32 accumulate, dense [H-16].
  These whitepaper numbers reached us only through secondary sources (PDF host unreachable).

**NVMe: PCIe 5.0 x4.** Class leaders are rated at 14.8 GB/s sequential read (Samsung 9100 PRO datasheet [H-20]) and ~14.5 GB/s (Crucial T705, via press
[H-21]). StorageReview measured 14.6 GB/s on the 9100 PRO [H-21]. The PCIe 5.0 x4 link peak is 15.75 GB/s (§3), so these drives run at ~94 % of the link.

### 2. Server hardware facts (i5-11500, Proxmox VE)

- **CPU.** Rocket Lake-S: 6 cores/12 threads, 2.7/4.6 GHz, 12 MB cache, 65 W TDP. DDR4-3200 dual-channel, 50 GB/s max (Intel's figure). PCIe 4.0 with 20 CPU lanes.
  Intel DL Boost: yes [H-22]. CPU-Monkey lists AVX-512 [H-23]. On Rocket Lake, DL Boost means AVX512_VNNI (inference; verify the flags with `lscpu`).
- **iGPU: UHD Graphics 750** [H-83]. Intel's compute-runtime README lists Rocket Lake as a supported integrated platform in the regular (non-legacy) packages,
  with OpenCL 3.0 and Level Zero 1.18; only Gen8/9/11 moved to "legacy1" [H-27]. Phoronix reported that release 24.39 disabled Rocket Lake [H-28] (see Contradictions).
  OpenVINO lists "Intel UHD Graphics" generically [H-29]. Vulkan (Mesa ANV) support is expected but not verified.
- **Virtualization of the iGPU.** Intel's matrix lists Rocket Lake under GVT-g, not SR-IOV (SR-IOV starts with 12th gen) [H-24]. An Intel community responder
  said GVT-g is not supported on Rocket Lake [H-25], contradicting the matrix. Proxmox users report SR-IOV (i915-sriov-dkms) failing on Rocket Lake, and Code 43
  when passing the iGPU through to a Windows guest [H-26]. Net result: whole-device passthrough to one VM, or (hypothesis) a `/dev/dri` bind-mount into LXC.
- **Proxmox VE.** 9.2 was released 2026-05-21. It is based on Debian 13.5 and ships **Linux 7.0** as the default kernel, plus QEMU 11.0, LXC 7.0 and ZFS 2.4
  [H-30][H-31]. 9.1 shipped kernel 6.17 [H-31]. A newer point release by 2026-11-26 could not be ruled out (search budget exhausted).

### 3. Bandwidth arithmetic for memory-bound decode (theoretical, not benchmarks)

For batch-1 autoregressive decode, every generated token reads all active weights once, plus the KV cache. The theoretical ceiling is
**tokens/s ≤ BW / B_tok**, where B_tok = active weight bytes + KV bytes read per token. If weights are split across tiers, the
time per token is Σ(bytes_i / BW_i). The peaks below come from datasheet inputs:

| Tier | Inputs (source) | Formula | Theoretical peak |
|---|---|---|---|
| RTX 4090 GDDR6X | 384-bit, 21 Gbps/pin [H-12][H-13] | 384 × 21e9 / 8 | **1008 GB/s** |
| DDR5-5200, 2 ch (official 1DPC) | [H-01][H-85] | 2 × 8 B × 5.2e9 | **83.2 GB/s** |
| DDR5-6000 EXPO (overclock) | [H-09] | 2 × 8 B × 6.0e9 | 96.0 GB/s |
| DDR5-3600, 4 DIMMs (2DPC) | [H-01][H-10] | 2 × 8 B × 3.6e9 | 57.6 GB/s |
| Zen 4 CCD→IOD read, one CCD | 32 B/clk, FCLK 2.0 GHz [H-08] | 32 × 2.0e9 | 64 GB/s per CCD |
| PCIe 4.0 x16 (GPU link) | 16 GT/s, 128b/130b [H-84] | 16e9 × 16 × 128/130 / 8 | **31.5 GB/s** per direction |
| PCIe 5.0 x4 (NVMe link) | 32 GT/s, 128b/130b [H-84] | 32e9 × 4 × 128/130 / 8 | 15.75 GB/s |
| NVMe device read | datasheet [H-20] | — | 14.8 GB/s |
| DDR4-3200, 2 ch (i5-11500) | [H-22] | 2 × 8 B × 3.2e9 | 51.2 GB/s (Intel lists 50) |
| Ethernet 1 / 2.5 / 10 GbE | line rate | Gbit/s / 8 | 0.125 / 0.31 / 1.25 GB/s |

Illustrative decode bounds. Model sizes assume ~4.9 effective bits per weight for 4-bit k-quant-style files; **this is an assumption, so check per file**.
KV is ignored here, which makes these upper bounds:

| Case | B_tok (GB) | VRAM 1008 | DDR5-5200 | DDR5-6000 | DDR4-3200 | PCIe 4.0 stream |
|---|---|---|---|---|---|---|
| 8B dense, ~4.9 bpw | 4.9 | 206 t/s | 17.0 | 19.6 | 10.4 | 6.4 |
| 8B dense, BF16 | 16.0 | 63 | 5.2 | 6.0 | 3.2 | 2.0 |
| 32B dense, ~4.9 bpw | 19.6 | 51 | 4.2 | 4.9 | 2.6 | 1.6 |
| 70B dense, ~4.9 bpw, 21 GB VRAM + 21.9 GB DRAM | 42.9 | — | 3.5 (=1/(21/1008+21.9/83.2)) | 4.0 | — | 1.4 if DRAM part streamed over PCIe |
| MoE gpt-oss-120b: 5.1B active [H-82], ~4.25 bpw MXFP4 (assumed), experts in DRAM | ≈2.7 | (does not fit) | ≈31 | ≈35 | — | — |

- **KV cache term.** Per-token KV = 2 × layers × KV-heads × head_dim × bytes. A typical 8B-class GQA configuration (assumed: 32 layers, 8 KV heads,
  head_dim 128, FP16) gives 128 KiB per token. At 8,192 tokens of context, each decode step reads an extra ~1 GiB. The 8B Q4 VRAM bound then falls from 206 to ~168 t/s.
- **Roofline check.** 165.2 TFLOPS (FP16, FP32 accumulate, GeForce [H-16]) / 1008 GB/s ≈ 164 FLOP/byte. Batch-1 decode at 4.9 bpw does ≈ 2 FLOP / 0.61 B
  ≈ 3.3 FLOP/byte, about 50× below the ridge. Decode stays memory-bound until batch sizes in the tens (theoretical; dequantization cost is ignored).
- **Why measured numbers will be lower.** (1) Real DRAM/GDDR efficiency is below 100 % (refresh, bank conflicts, read/write turnaround), so it must be measured
  (STREAM-like, nvbandwidth). (2) The per-CCD fabric ceiling applies [H-08]. (3) Kernel launch and CPU↔GPU synchronization overheads add up; the KTransformers
  authors identify synchronization as what limits hybrid systems [H-57]. (4) Dequantization and attention compute take time. (5) PCIe protocol overhead
  (TLP/DLLP framing) cuts into the link rate. (6) KV reads grow with context. (7) Power and thermal limits apply (450 W TGP [H-12]). (8) Threads may not be pinned well (§7).

### 4. Model-loading path

**Formats and mmap.**
- A safetensors file is an 8-byte header length, a JSON header (100 MB cap) with dtype/shape/offsets, then a contiguous little-endian byte buffer.
  There is no pickle, so no arbitrary code runs on load [H-33].
- The README admits "no format is really zero-copy": GPU loading always needs a copy. It recommends memory-mapping with torch or
  `torch.UntypedStorage.from_file` to avoid an extra CPU copy [H-33].
- llama.cpp loads GGUF via mmap; the approach was introduced in 2023 [H-34]. The current CLI default load mode is mmap [H-35].

**Page cache vs O_DIRECT.**
- mmap and buffered reads are fast on repeated loads, because the page cache keeps the model. Cold loads and memory pressure make load times erratic,
  and a cached model competes for RAM with CPU-offloaded weights.
- llama.cpp merged async O_DIRECT loading on 2025-12-18. The stated aim was consistent load time at sequential disk speed [H-36].
- A `--direct-io` flag followed. It was then **disabled by default** (#19109) [H-37], and is now deprecated in favour of `--load-mode` [H-35].
- ServerlessLLM loads with O_DIRECT into a pinned memory pool [H-44].

**Accelerated loaders.** All gains below are the sources' own claims, in their own setups:
- **fastsafetensors** (IBM; IEEE CLOUD 2025, arXiv 2505.23072) [H-42]: claims 4.8–7.5× faster loading than the default safetensors deserializer, and 26.4 GB/s
  for Llama-70B on 4 GPUs with GDS. It has a no-GDS path (ROCm: 6.02 vs 1.28 GB/s against mmap for GPT-2 Medium) and integrates as `vllm serve --load-format fastsafetensors`.
- **Model Streamer** (formerly Run:ai Model Streamer; renamed 2026-09-13) [H-43]: a multi-threaded C++ engine reads storage into CPU buffers while the application
  copies tensors to the GPU as they arrive. Backends: local/network file systems, S3, GCS, Azure. Apache-2.0. The README shows no numbers.
- **ServerlessLLM** (OSDI'24) [H-44]: sequential-read-optimized checkpoint format, O_DIRECT, pinned pool, parallel loading. Claims 6–10× over the SafeTensors loader
  on H100 with NVMe (e.g. Qwen3-32B: 20.6 s vs 3.2 s random, 12.5 s vs 1.3 s cached).
- **io_uring.** No loader-specific evidence was gathered in this pass. Async direct I/O in llama.cpp [H-36] and the parallel pinned-buffer designs above are
  the relevant precedents. An io_uring loader is a hypothesis (O-H-05).
- **Pinned host buffers.** Copies from pageable memory go through a driver staging copy, while pinned buffers allow direct DMA (standard CUDA behaviour; not
  re-verified this pass). All three loaders use pinned buffers or pools [H-42][H-43][H-44]. In vLLM's weight offload, pinning can use ~1.9× the requested host memory
  because allocations round up [H-62].

**GPUDirect Storage (cuFile).**
- Support and modes:
  - The v1.19.1 release notes (2026-09-17) state that GDS mode applies to **"Data Center and Quadro (desktop) cards with compute capability > 6"**,
    and **all other cards run only in compatibility mode** [H-45]. Older guides said Tesla/Quadro only [H-48].
  - So an RTX 4090 gets cuFile **compatibility mode**, which is a CPU bounce-buffer path. It is meant for development without supported hardware, and
    can be forced with `force_compat_mode` [H-47]. This is our inference from the matrix: no source names GeForce explicitly.
  - Two paths for real GDS:
    - **nvidia-fs.ko**: v2.30 docs dated 2026-09-08 [H-49]. v1.19 added kernel 7.0 support [H-45].
    - **PCI P2PDMA mode**: since CUDA 12.8, with no nvidia-fs and no patched nvme.ko. It needs the OpenRM driver 570+ with registry keys,
      `use_pci_p2pdma=true` (default false) and a kernel with P2PDMA [H-46].
- Kernel requirements:
  - The overview gives ≥6.2 for Ubuntu and 5.14 for RHEL 9.4 [H-46].
  - RAID1/10 under P2PDMA needs kernel ≥7.2. RAID0 and multipath are unsupported [H-45].
  - The release notes list that **NVMe P2PDMA can fail on x86_64 with KASLR enabled** [H-45].
- Conclusion for this hardware: GDS gives no supported zero-copy path on the 4090. Do not trade away KASLR for it (**H-H-03**).

**Weight compression and dedup.**
- **ZipNN** (arXiv 2411.05239) [H-50]: lossless compression that exploits low-entropy exponent bits. BF16 models shrink to ~66 % of their size, "clean" models to
  under half. On BF16 it gets a 17 % better ratio than zstd and runs 62 % faster. It claims up to 80 GB/s decompression with many threads (hardware not seen in the snippet).
  A follow-up extends the method to FP8/FP4 [H-51].
- **Hugging Face Xet** [H-52][H-53]: content-defined chunking (~64 KB chunks) packed into 64 MB blocks in a content-addressed store. `hf_xet` has been auto-installed
  since huggingface_hub 0.32.0, and "well over a dozen PB" has been migrated. It is the reference design for a deduplicated local model store (O-H-07).

**File systems for model stores.**
- **bcachefs** was marked externally maintained in 6.17 and **removed from mainline in 6.18**. It is now DKMS-only [H-55], so it is not acceptable as a default.
- **OpenZFS 2.3** added Direct I/O that bypasses the ARC (`zfs_dio_enabled`) [H-56]. Proxmox 9.2 ships ZFS 2.4 [H-30]. ZFS's ARC is separate from the
  Linux page cache. Whether mmap'd GGUF on ZFS is cached twice is unverified (open question).
- **XFS or ext4** on a dedicated NVMe is the conservative default (hypothesis; GDS's current supported-FS list was not re-verified).

**GPU process checkpoint/restore.**
- How it works: `cuda-checkpoint` suspends CUDA state. It drains submitted work, copies device memory to host and releases the GPU. CRIU then dumps and
  restores the process [H-54].
- Driver levels: driver 550+; 570 adds CRIU 4.0 integration and NVML support; 580 adds GPU migration; 610 adds CUDA IPC-handle support [H-54].
- Limitation: no UVM or shareable-handle IPC memory [H-54].
- Use for us: restore a *warmed* server (after graph capture/compile) instead of a cold start (**H-H-05**).

**Load-time arithmetic (theoretical).**
- Cold 20 GB from NVMe: 20/14.8 = **≥1.35 s**. The host-to-device copy alone is 20/31.5 = 0.63 s, so a pipelined loader is NVMe-bound. Done serially, it is ≥1.98 s.
- Warm from page cache with pinned buffers: ≥0.63 s.
- From a NAS: 160 s over 1 GbE, 16 s over 10 GbE.
- A ZipNN-compressed BF16 checkpoint at 66 % size: ≥0.89 s from NVMe, **if** decompression sustains ≥22 GB/s of output (**H-H-04**).

### 5. RAM/VRAM tiering and offload (24 GB VRAM + 64 GB RAM)

- **llama.cpp.**
  - `-ngl` puts layers on the GPU, and `--tensor-split` sets the device ratio.
  - For MoE, `-ot "exps=CPU"` keeps experts on the CPU, and **`--cpu-moe` is its shortcut**. **`--n-cpu-moe N`** keeps the MoE weights of the first N layers on the CPU
    [H-35][H-38][H-39].
  - CUDA graphs are disabled when `--n-cpu-moe` is used (#18593) [H-38]. llama-bench supports the flag (#15952) [H-38].
  - A blog reports a newer GPU cache for host-resident experts (`--moe-cache-mib`, PR #29887) with a 2.2× decode gain on an RTX 5090 [H-40]. **Unverified.**
- **KTransformers** (SOSP'25) [H-57][H-58]:
  - Built for hybrid CPU/GPU MoE inference. It uses AMX/AVX-512 CPU expert kernels, the decode stage wrapped in one CUDA Graph, and
    "expert deferral" that overlaps CPU experts with the next layer's attention.
  - It is integrated into SGLang.
  - The reported speed-ups (prefill 4.62–19.74×, decode 1.25–4.09×) come from a secondary mirror.
  - On Zen 4 only the AVX-512 path applies.
- **vLLM.**
  - `--cpu-offload-gb` treats host RAM as virtual VRAM, for example 24 GB + 10 → "34 GB". Offloaded weights cross PCIe **on every forward pass**, so it
    "requires fast CPU-GPU interconnect" [H-59]. `--offload-backend uva|prefetch` adds asynchronous prefetching [H-59].
  - Community reports: a ~19× slowdown if offload is enabled even though the model fits [H-63], and 1.9× host memory overhead [H-62].
  - KV offload uses the `OffloadingConnector` (`cpu_bytes_to_use`). It extends the prefix cache into host RAM and optional further tiers [H-60][H-61].
- **CUDA Unified Memory oversubscription.**
  - Performance "can vary up to 100×" with platform, oversubscription factor and hints [H-65].
  - Memory advice gives up to 25 % at 50 % oversubscription on some platforms and slowdowns on others [H-66].
  - Fault handling costs ~10s of µs per page while execution stalls [H-67].
  - Not a sane default for weight tiering. Explicit placement wins.
- **What fits sensibly.** Usable VRAM is ~21–22 GB after context and KV (hypothesis). Usable RAM is ~56 GB after OS and services (hypothesis).

| Class | Example | Placement | Theoretical decode bound (§3) | Verdict |
|---|---|---|---|---|
| ≤ ~20 GB of weights | 8B–32B dense at 4–5 bpw; gpt-oss-20b (21B total / 3.6B active, "within 16 GB" [H-82]) | all in VRAM | 51–206 t/s | sweet spot |
| 20–60 GB dense | 70B at ~4.9 bpw | layer split VRAM/DRAM | ~3.5–4 t/s | usable for batch-1, DRAM-bound |
| Large MoE, ~60–75 GB | gpt-oss-120b (117B / 5.1B active; MXFP4 experts; "single 80 GB GPU" [H-82]) | dense and attention in VRAM, experts in RAM | ≤ ~31–35 t/s | best value but **RAM-borderline** (H-H-06) |
| > ~80 GB | — | mmap paging from NVMe | ≤ 14.8 GB/s ÷ B_tok | not interactive |

### 6. Distributed and heterogeneous compute across both machines

- **llama.cpp RPC** [H-41]:
  - `rpc-server` exposes a remote backend, and the client lists servers with `--rpc`. Weights and KV are split in proportion to device memory, or by `--tensor-split`.
  - `-c` keeps a server-side tensor cache, so large tensors are not resent on every load. RDMA (RoCEv2) is negotiated, with TCP as the fallback.
  - The README calls it a **proof of concept, "fragile and insecure"**: never run it on open networks.
- **exo** [H-68]: auto-discovery and topology-aware tensor/pipeline parallelism on MLX. On Linux it "runs on CPU". GPU support for Linux is "under development". Not useful
  for the 4090 today.
- **distributed-llama** [H-69]: tensor parallel over Ethernet with 2^n nodes, limited by KV heads. Formats are Q40 weights with Q80 buffers, or F32. CPU, with experimental Vulkan.
- **vLLM multi-node** [H-64]: requires Ray. The convention is TP = GPUs per node and PP = number of nodes. The model must be present on each node or on shared storage.
  Mixed hardware is not documented. The server has no GPU anyway.
- **Petals** [H-70]: BitTorrent-style public swarm. Its maintenance status in 2026 is unclear, and it assumes a public network, which conflicts with local-first.
- **Network arithmetic (theoretical).**
  - Pipeline-split decode sends one hidden-state vector per token per boundary, e.g. 8,192 × 2 B = 16 KiB. Bandwidth is irrelevant; latency and the slowest
    stage dominate.
  - Tensor parallel needs collectives every layer, so it is latency-bound on 1 GbE (hypothesis).
  - The initial weight push costs model size ÷ link speed, e.g. 20 GB at 1 GbE ≥160 s. The RPC cache helps here [H-41].
- **Assessment.** The server's 51.2 GB/s of DDR4 is below the workstation's 83.2 GB/s. Any model that fits in workstation VRAM+RAM gets slower when split
  (**H-H-07**).

**Realistic roles for the i5-11500:**

| Role | Why it fits |
|---|---|
| Benchmark controller / orchestrator | Keeps load generation, logging and analysis off the device under test |
| Energy logger | Polls the wall meter and stores NVML/RAPL exports from the workstation (§8) |
| CI runner | AVX-512 + VNNI x86 build/test coverage, Intel Gen12 iGPU (OpenCL/Level Zero/OpenVINO/Vulkan) backend coverage [H-27][H-29] |
| CPU embeddings / ASR / rerankers | Small models, memory-bound at 51.2 GB/s; iGPU offload possible via OpenVINO (hypothesis) |
| Model cache / artifact store | Distribution and backup. Too slow for hot loads over 1 GbE (§4 arithmetic) |
| Agent services, vector DB | Latency-tolerant and CPU-light |
| Ubuntu-baseline VM | Stock-distribution reference for A/B comparisons (VM caveats in §7) |

### 7. Proxmox measurement caveats

- **CPU type.**
  - The backend default is `kvm64`, and the UI default is `x86-64-v2-AES`. The guide recommends `host` for maximum performance when live migration is not needed [H-32].
  - Per the x86-64 psABI levels [H-86], v2 has no AVX/AVX2 (they arrive in v3) and no AVX-512 (v4). A non-`host` guest will run llama.cpp and BLAS
    CPU paths without AVX2/AVX-512 (**H-H-09**).
- **KSM.**
  - Enabled by default, with per-VM `allow-ksm` [H-32]. Merging scans pages in the background and can un-merge on write, which adds noise. The guide itself
    advises considering disabling it for hosting.
  - For benchmarks: `systemctl disable --now ksmtuned` and `qm set <id> --allow-ksm 0`.
- **Ballooning.** On by default, with auto-ballooning towards an 80 % host target [H-32]. Disable it, and use fixed memory, for benchmark VMs.
- **Huge pages.** `hugepages: 2|1024|any`, plus `keephugepages` [H-32]. Reserve them on the host at boot so that allocation does not fail or fragment.
- **NUMA.** Both machines are single-socket, so per-node binding matters less than core pinning (hypothesis). Proxmox's NUMA option is still required for hotplug [H-32].
- **VM vs LXC.** LXC shares the host kernel (Proxmox's 7.0 kernel [H-30]). It avoids virtualization overhead, but it measures *Proxmox's* kernel, not the
  candidate distribution's. VMs measure the guest kernel but add virtualization effects. Use LXC for services and VMs for distribution baselines.
- **Clocksource and timers.** Guests typically use kvm-clock. Time on the host or on a separate controller, and log the guest clocksource (unverified this pass).
- **Noisy neighbours.** Stop other guests. Pin vCPUs to dedicated cores. Record the host governor, microcode and kernel. Repeat runs and report variance.

### 8. Energy measurement

- **NVML / nvidia-smi.**
  - On A100/H100, only ~25 % of runtime is actually sampled for power. The same kernel can read 80–200 W [H-71].
  - On some drivers `power.draw` is a 1-s average, with a separate instant value exposed [H-72].
  - The behaviour on Ada GeForce is undocumented in our sources (**H-H-08**). Short requests are most at risk, so use long, repeated runs and
    cross-check against inline 12V/slot power or a wall meter.
- **AMD RAPL (Zen 4).**
  - Zen 2 study: there is no DRAM domain, DRAM is not (fully) in the package domain, and RAPL "does not correctly represent the impact of data" on
    power [H-73]. CERN reports RAPL reading lower than IPMI on Milan/Rome [H-81].
  - Per-core AMD RAPL patches were tested on Zen 2–4 servers [H-75].
  - No Zen 4 client validation was found.
  - After CVE-2020-12912 the hwmon `amd_energy` attributes are restricted to owner and group [H-74]. Its upstream status in 7.x is not verified.
- **Intel RAPL (Rocket Lake).**
  - Intel-side accuracy studies (Haswell/Skylake era) fit RAPL against external meters [H-80].
  - After PLATYPUS (CVE-2020-8694/8695) [H-78], unprivileged access to powercap `energy_uj` was removed (upstream kernel fix, not re-read here). Distributions
    document a chmod 400 mitigation [H-76][H-77][H-79].
  - Intel also changes how energy is reported when SGX is enabled [H-76].
  - Which RAPL domains Rocket Lake exposes (DRAM? psys?) is unverified.
- **Policy.** Never make `energy_uj` world-readable. Run a small privileged collector that exports aggregated energy to an authorised group.
- **Wall meter.** A logging wall meter captures PSU losses, fans, board and DRAM. Use it as the system-level ground truth and to calibrate NVML and RAPL sums
  (accuracy class to be chosen; no source consulted).

## Optimization opportunities

| ID | Level | Opportunity | Expected benefit | Feasibility | Risk | Effort | Evidence | Novelty |
|---|---|---|---|---|---|---|---|---|
| O-H-01 | runtime | CCD-aware placement: memory-bound CPU work (offloaded layers/experts) spans both CCDs; cache-sensitive services (tokenizer, scheduler, small embedders) on the V-Cache CCD | M: per-CCD fabric ceiling 64 GB/s < 83–96 GB/s DRAM | High (cpusets, `amd_3d_vcache`) | Low | S | [H-08][H-05][H-03] | known-elsewhere |
| O-H-02 | userspace | Memory-config guidance + validator: 2 DIMMs 1DPC; detect 2DPC/JEDEC fallback; flag EXPO as out-of-spec | M: +15 % theoretical BW 5200→6000; avoids −31 % with 4 DIMMs | High | EXPO stability | S | [H-01][H-09][H-10] | established |
| O-H-03 | runtime | Automatic MoE fit planner choosing `--n-cpu-moe`/`-ot` per model from VRAM, RAM and context | H: makes ~60–75 GB MoE usable at DRAM-bound speeds | High | Low | M | [H-35][H-38][H-39] | known-elsewhere |
| O-H-04 | runtime | Batch-aware offload switch: compute on CPU for batch-1 decode, stream weights to GPU (prefetch backend) for prefill and large batches | M–H: picks the better of 83 vs 31.5 GB/s per phase | Medium | Complexity | M | [H-59][H-57] | known-elsewhere |
| O-H-05 | userspace | Unified model loader service: parallel O_DIRECT/io_uring reads into pinned ring buffers; mmap for hot models; page-cache budget | M: cold-load near NVMe rate; protects RAM for offload | Medium | Low | M | [H-36][H-42][H-43][H-44] | known-elsewhere |
| O-H-06 | kernel | GDS evaluation on 4090 (compat vs nvidia-fs vs P2PDMA), never disabling KASLR | L: GeForce is compat-only; single NVMe already bounds | Low | Security if mis-tuned | S | [H-45][H-46][H-47] | established |
| O-H-07 | userspace | Content-addressed local model store (Xet-style CDC chunks; hard-links/reflinks for identical files) | M storage, L speed | Medium | Integrity bugs | L | [H-52][H-53] | known-elsewhere |
| O-H-08 | userspace | Lossless checkpoint compression (ZipNN) for BF16 cold loads and NAS transfer | M: up to ~1/3 fewer bytes for BF16 | Medium | CPU cost; little gain for 4-bit files | M | [H-50][H-51] | known-elsewhere |
| O-H-09 | runtime | Warm-start snapshots via cuda-checkpoint + CRIU for long-init servers | M–H: skips compile and graph capture | Medium (driver ≥570, no UVM) | Snapshot staleness | M | [H-54] | known-elsewhere |
| O-H-10 | runtime | Host-RAM KV tier for agent prefix reuse (vLLM OffloadingConnector) | M for multi-turn agents | High | RAM competition | S | [H-60][H-61] | established |
| O-H-11 | application | i5-11500 as controller, energy logger and CI runner (AVX-512 + Gen12 iGPU coverage) | M: cleaner measurements, wider CI | High | Low | S | [H-22][H-27] | established |
| O-H-12 | userspace | Reproducible Proxmox benchmark profile (CPU `host`, KSM/balloon off, hugepages, pinning, recorded clocksource) | M (validity, not speed) | High | Low | S | [H-32][H-86] | established |
| O-H-13 | userspace | Energy telemetry daemon: high-rate NVML (instant + average), RAPL via privileged collector, wall-meter calibration | M (validity) | High | Side-channel if mis-permissioned | M | [H-71][H-73][H-76] | known-elsewhere |

## Hypotheses

Each row is a **Hypothesis H-H-NN** with its statement, rationale, falsification test and risk.

| ID | Statement | Rationale | Test to falsify | Risk |
|---|---|---|---|---|
| **Hypothesis H-H-01** | CPU-side memory-bound decode confined to one 7950X3D CCD cannot exceed ~64 GB/s. Spanning both CCDs approaches the DRAM limit. V-Cache gives no measurable gain for weight streaming | 32 B/clk per-CCD read path [H-08]; weights ≫ 96 MB | STREAM-like read and llama-bench with partial offload under `taskset` (CCD0, CCD1, both) at DDR5-5200 and 6000 | Cross-CCD sync may offset the gain |
| **Hypothesis H-H-02** | Batch-1 decode with partial offload: llama.cpp-style CPU compute of offloaded layers beats vLLM-style PCIe weight streaming. Above some batch/prefill size, streaming wins | 83.2 vs 31.5 GB/s theoretical (§3); arithmetic intensity grows with batch | Same model and quantization, batch 1–64 sweep, tokens/s | Different kernels and formats confound |
| **Hypothesis H-H-03** | GDS (compat, nvidia-fs or P2PDMA) gives no load-throughput gain over a pinned-buffer parallel loader on this workstation. The best case is lower CPU use | GeForce is compat-only [H-45]; one NVMe (14.8 GB/s) bounds the path | `gdsio`/fastsafetensors with and without GDS; time + CPU-seconds | P2PDMA on GeForce unsupported/unstable |
| **Hypothesis H-H-04** | ZipNN cuts BF16 cold-load time ≈ in proportion to size reduction (≈30 %) if 16 cores decompress at ≥ NVMe rate; ≈0 % for ≤4-bit GGUF | [H-50]; quantized weights have high entropy | Drop caches; load(raw) vs load(compressed), BF16 and Q4 | CPU contention during load |
| **Hypothesis H-H-05** | cuda-checkpoint + CRIU restore of a warmed vLLM/SGLang process beats a cold start for ≤20 GB models; restore ≈ image size / storage BW | [H-54]; cold start includes compile and graph capture | TTFT cold vs restore, 3 model sizes | Driver coupling; UVM breaks the checkpoint [H-54] |
| **Hypothesis H-H-06** | gpt-oss-120b with experts in RAM is borderline on 64 GB: major faults and RSS pressure degrade long sessions; 96–128 GB would be comfortable | 117B × ~4.25 bpw ≈ 62 GB (theoretical; MXFP4 overhead assumed) [H-82] | `--cpu-moe`; log RSS, major faults, t/s over 30 min | OOM kills during the test |
| **Hypothesis H-H-07** | Splitting any model that fits workstation VRAM+RAM across both machines (llama.cpp RPC, 1/2.5 GbE) lowers tokens/s; distribution only adds capacity | 51.2 vs 83.2 GB/s; per-token network round trips | Same model local vs RPC split; per-token latency breakdown | Keep RPC on an isolated network [H-41] |
| **Hypothesis H-H-08** | NVML on RTX 4090 shows averaging/partial sampling like A100/H100; per-request energy for requests under 1 s is off by >10 % | [H-71][H-72] | NVML-integrated energy vs inline 12V/slot meter, pulse-width-varied kernels | Needs extra measurement hardware |
| **Hypothesis H-H-09** | With Proxmox's default CPU type, guests lack AVX2/AVX-512 and CPU inference is far slower than with `host` | [H-32][H-86] | `grep -o 'avx[^ ]*' /proc/cpuinfo`; llama-bench under both types | None |
| **Hypothesis H-H-10** | On Zen 4, double-pumped AVX-512 (VNNI/BF16) mainly speeds up prefill (compute-bound) and barely changes DRAM-bound decode vs AVX2 | [H-07][H-06]; decode is bandwidth-bound (§3) | llama.cpp built with and without AVX-512; pp512 vs tg128 | Build-flag confounders |

## Contradictory or weak evidence

- **7950X3D PCIe lanes:** 28 (AMD) vs 24 (TechPowerUp). Resolved as 28 total and 24 usable [H-01][H-02]. Power: PPT 162 W [H-02] vs "170 W PL2" (Intel terminology) [H-90].
- **Zen 4 per-CCD read ceiling:** 64 GB/s at FCLK 2 GHz [H-08] vs 59.7 GB/s computed in an HN comment [H-89]. A forum report measured ~73 GB/s read with 2 CCDs at DDR5-6000 [H-87] (D).
- **Ada FP8 throughput:** the original whitepaper figures (661/1321 TFLOPS FP8 [H-12]) vs the revised half-rate FP32 accumulate on GeForce, known only via forum posts [H-16].
  The whitepaper host was unreachable.
- **Rocket Lake GVT-g:** Intel's matrix lists GVT-g [H-24]. An Intel community responder says it is not supported [H-25]. The upstream i915 GVT-g targets older generations (unverified).
- **compute-runtime and Rocket Lake:** Phoronix says 24.39 disabled Rocket Lake [H-28]. The current README lists it as supported [H-27].
- **GDS P2PDMA kernel minimum:** ≥6.2 for Ubuntu, 5.14 for RHEL 9.4 [H-46], and ≥7.2 for RAID1/10 [H-45]. These are different scopes, but they are easy to misread.
- **nvidia-smi "ghost power":** a blog anecdote says it is A100-specific [H-88]. The arXiv study shows partial sampling on H100 too [H-71].
- **Weak sources:** KTransformers speed-ups (secondary mirror) [H-57]; llama.cpp `--moe-cache-mib` (blog) [H-40]; vLLM offload overheads (single GitHub issues) [H-62][H-63];
  DDR5 4-DIMM latency figures (unverified guide, not used); Petals 2026 status [H-70].

## Open questions

1. Which logical CPUs are on the V-Cache CCD of our unit, and does `amd_3d_vcache` "cache" mode change placement for our services?
2. What are the achievable read bandwidths on our board for DDR5-5200 vs 6000, one CCD vs two? (Phase 1 measurement.)
3. What BAR1 size does the 4090 get with ReBAR on our board, and does cuFile ever leave compat mode on GeForce?
4. Does the RTX 4090 expose an NVML total-energy counter, and at what update rate? How do `power.draw.average` and `.instant` behave on Ada?
5. What is Zen 4 client RAPL accuracy versus a wall meter? What is the upstream status of `amd_energy` in 7.x, and which RAPL domains does Rocket Lake expose?
6. What is the current Proxmox VE point release on 2026-11-26 (9.2 or newer)?
7. Does mmap'd GGUF on ZFS cache twice (ARC + page cache)? Is OpenZFS Direct I/O usable for model stores?
8. What is GDS's current supported filesystem list (ext4/XFS) for the nvidia-fs and P2PDMA paths?
9. Do OpenVINO GPU and Level Zero work for UHD 750 inside an LXC container on the Proxmox 7.0 kernel?
10. Has exo gained Linux CUDA support? Is Petals still maintained?
11. Is llama.cpp's newer `--load-mode`/MoE expert cache stable, and what does it default to?

## Access limitations

- **WebFetch DNS failures (not retried):** `www.amd.com` (7950X3D spec page), `images.nvidia.com` (Ada whitepaper PDF), `www.proxmox.com` (9.2 press release),
  `docs.nvidia.com` (GDS docs). The caller reported `docs.kernel.org` as failing; it was not attempted. For these hosts, content was taken from
  search-engine summaries of the same URLs. Their grade is kept, but confidence is lower.
- **Search budget exhausted:** after ~37 searches, the shared per-turn web-search limit was reached. The following were not searched: the x86-64 psABI text, DDR5 JEDEC
  text, PCI-SIG rates, the "RAPL in Action" full text, Intel RAPL domains on Rocket Lake, OpenZFS Direct I/O semantics with mmap, io_uring loaders,
  Proxmox clocksource guidance, and wall-meter accuracy classes. [H-84][H-85][H-86] are cited as standard specifications that were *not fetched*.
- **Fetched directly (github.com):** tinygrad P2P fork, compute-runtime, fastsafetensors, Model Streamer, ServerlessLLM, cuda-checkpoint, llama.cpp RPC
  README, exo, distributed-llama, gpt-oss, OpenZFS 2.3.0 release, safetensors.

## Sources

| ID | Title | URL | Date | Accessed | Grade | Notes |
|---|---|---|---|---|---|---|
| H-01 | AMD Ryzen 9 7950X3D product specifications | https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7950x3d.html | n.d. | 2026-11-26 | A | Fetch failed (DNS); specs via search summary |
| H-02 | TechPowerUp CPU DB: Ryzen 9 7950X3D | https://techpowerup.com/cpu-specs/ryzen-9-7950x3d.c3024 | n.d. | 2026-11-26 | C | PPT 162 W; "24 lanes" |
| H-03 | AnandTech: The AMD Ryzen 9 7950X3D Review | https://www.anandtech.com/show/18747 | 2023 | 2026-11-26 | C | 96/32 MB L3 split; scheduling |
| H-04 | TechPowerUp: Ryzen 9 7950X3D Review | https://techpowerup.com/review/amd-ryzen-9-7950x3d/2.html | 2023 | 2026-11-26 | C | CCD clocks (snippet) |
| H-05 | Phoronix: AMD 3D V-Cache Optimizer driver for Linux 6.13 | https://www.phoronix.com/news/AMD-3DV-Optimizer-Linux-6.13 | 2024 | 2026-11-26 | C | sysfs frequency/cache modes |
| H-06 | Phoronix: AMD Zen 4 AVX-512 Performance Analysis (7950X) | https://www.phoronix.com/review/amd-zen4-avx512/1 | 2022 | 2026-11-26 | C | cpuinfo flags incl. avx512_vnni/bf16 |
| H-07 | TechInsights: AMD Zen 4 Adds AVX-512 | https://www.techinsights.com/blog/amd-zen-4-adds-avx-512 | n.d. | 2026-11-26 | C | double-pumped 256-bit |
| H-08 | Chips and Cheese: AMD's Zen 4 Part 3, System Level Stuff | https://chipsandcheese.com/?p=11867 | 2022 | 2026-11-26 | C | 32 B/clk read per CCD (snippet) |
| H-09 | Tom's Hardware: AMD confirms DDR5-6000 sweet spot | https://www.tomshardware.com/news/amd-confirms-ddr5-6000-ram-is-the-sweet-spot-for-ryzen-7000-cpus | 2022 | 2026-11-26 | C | |
| H-10 | AnandTech: Ryzen 9 7950X review, DDR5 & EXPO page | https://at-web1.www.anandtech.com/show/17585/amd-zen-4-ryzen-9-7950x-and-ryzen-5-7600x-review-retaking-the-high-end/5 | 2022 | 2026-11-26 | C | 2DPC → DDR5-3600 |
| H-11 | NVIDIA Ada GPU Architecture whitepaper | https://images.nvidia.com/aem-dam/Solutions/geforce/ada/nvidia-ada-gpu-architecture.pdf | 2022/23 | 2026-11-26 | A | Fetch failed (DNS); not read |
| H-12 | Tom's Hardware: Ada Lovelace and RTX 40-series, everything we know | https://www.tomshardware.com/features/nvidia-ada-lovelace-and-geforce-rtx-40-series-everything-we-know | n.d. | 2026-11-26 | C | 1008 GB/s, 450 W, FP8 661/1321 |
| H-13 | Wccftech: GeForce RTX 4090 roundup | https://wccftech.com/roundup/nvidia-geforce-rtx-4090 | n.d. | 2026-11-26 | C | 21 Gbps, 384-bit |
| H-14 | Puget Systems: RTX 4090 Founders Edition parts page | https://www.pugetsystems.com/parts/Video-Card/NVIDIA-GeForce-RTX-4090-24GB-Founders-Edition-14604/ | n.d. | 2026-11-26 | C | PCIe 4.0 x16; multi-GPU N/A |
| H-15 | NVIDIA: GeForce RTX 4090 product page | https://www.nvidia.com/en-sg/geforce/graphics-cards/40-series/rtx-4090/ | n.d. | 2026-11-26 | A | Resizable BAR (snippet) |
| H-16 | NVIDIA Dev Forums: Ada GeForce (RTX 4090) FP8 cuBLASLt performance | https://forums.developer.nvidia.com/t/ada-geforce-rtx-4090-fp8-cublaslt-performance/250737 | 2023 | 2026-11-26 | D | Reports revised whitepaper (half-rate FP32 acc.) |
| H-17 | Tom's Hardware: Nvidia confirms GeForce cards lack P2P support | https://www.tomshardware.com/news/nvidia-confirms-geforce-cards-lack-p2p-support | n.d. | 2026-11-26 | C | |
| H-18 | NVIDIA Dev Forums: Standard CUDA tests fail with dual RTX 4090 | https://forums.developer.nvidia.com/t/standard-nvidia-cuda-tests-fail-with-dual-rtx-4090-linux-box/233202/16 | 2022/23 | 2026-11-26 | C | NVIDIA rep: P2P unsupported on 4090 |
| H-19 | tinygrad/open-gpu-kernel-modules (P2P fork) | https://github.com/tinygrad/open-gpu-kernel-modules | n.d. | 2026-11-26 | B | Fetched; branch 565.57.01-p2p |
| H-20 | Samsung 9100 PRO spec sheet | https://images.samsung.com/is/content/samsung/p6pim/us/mz-vap2t0b-am/specsheet/us-specsheet-mz-vap2t0b-am-548821014.pdf | 2025 | 2026-11-26 | A | 14,800 MB/s read (snippet) |
| H-21 | StorageReview: Samsung 9100 PRO review | https://www.storagereview.com/?p=143224 | 2025 | 2026-11-26 | C | Measured 14.6 GB/s; T705 ~14.5 GB/s |
| H-22 | Intel ARK: Core i5-11500 specifications | https://www.intel.com/content/www/us/en/products/sku/212277/intel-core-i511500-processor-12m-cache-up-to-4-60-ghz/specifications.html | n.d. | 2026-11-26 | A | 50 GB/s, PCIe 4.0 ×20, DL Boost (snippet) |
| H-23 | CPU-Monkey: Intel Core i5-11500 | https://www.cpu-monkey.com/en/cpu-intel_core_i5_11500 | n.d. | 2026-11-26 | C | AVX-512 listed |
| H-24 | Intel: Graphics Virtualization Technologies Support per Graphics Family | https://www.intel.com/content/www/us/en/support/articles/000093216/graphics.html | n.d. | 2026-11-26 | A | RKL: GVT-g; SR-IOV from 12th gen |
| H-25 | Intel Community: Splitting integrated graphics on Rocket Lake | https://community.intel.com/t5/Graphics/Splitting-Integrated-graphics-to-multiple-VMs-on-rocket-lake/m-p/1495138 | 2023 | 2026-11-26 | C | Intel responder: not supported |
| H-26 | Proxmox forum: Rocket Lake iGPU passthrough or SR-IOV | https://forum.proxmox.com/threads/get-rocketlake-igpu-working-with-passthrough-or-sr-iov.167220/ | 2025 | 2026-11-26 | D | |
| H-27 | intel/compute-runtime README | https://github.com/intel/compute-runtime | n.d. | 2026-11-26 | B | Fetched; RKL supported; legacy1 = Gen8–11 |
| H-28 | Phoronix: Compute Runtime 24.39 disables Ice Lake & older | https://phoronix.com/news/Intel-CR-24.39.31294.12 | 2024 | 2026-11-26 | C | Lists RKL as disabled (contradiction) |
| H-29 | OpenVINO System Requirements | https://docs.openvino.ai/system_requirements | 2026 | 2026-11-26 | A | "Intel UHD Graphics" (snippet) |
| H-30 | Proxmox: Proxmox Virtual Environment 9.2 press release | https://www.proxmox.com/en/news/press-releases/proxmox-virtual-environment-9-2 | 2026-05-21 | 2026-11-26 | A | Fetch failed (DNS); via search summary |
| H-31 | Notebookcheck: Proxmox 9.2 launches with Linux 7.0 | https://www.notebookcheck.net/Proxmox-9-2-Virtual-Environment-launches-with-the-7-0-Linux-kernel-as-default.1303073.0.html | 2026 | 2026-11-26 | C | 9.1 = 6.17 |
| H-32 | Proxmox VE Administration Guide / qm(1) | https://pve.proxmox.com/pve-docs/pve-admin-guide.html | 2026 | 2026-11-26 | A | CPU type, KSM, ballooning, hugepages (snippet) |
| H-33 | huggingface/safetensors README | https://github.com/huggingface/safetensors | n.d. | 2026-11-26 | B | Fetched |
| H-34 | J. Tunney: mmap in llama.cpp | https://justine.lol/mmap | 2023-04-05 | 2026-11-26 | B | Seen in search results only |
| H-35 | llama.cpp tools/cli README | https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md | 2026 | 2026-11-26 | B | Flags via search summaries/mirrors |
| H-36 | llama.cpp PR #18012 (async Direct I/O), #18166 (`--direct-io`) | https://github.com/ggml-org/llama.cpp/pull/18012 | 2025-12-18 | 2026-11-26 | B | Seen via mirror |
| H-37 | llama.cpp PR #19109 "disable Direct IO by default" | https://github.com/ggml-org/llama.cpp/pull/19109 | 2026 | 2026-11-26 | B | Seen via mirror |
| H-38 | llama.cpp PRs #15077 (`--n-cpu-moe`), #18593, #15952 | https://github.com/ggml-org/llama.cpp/pull/15077 | 2025–26 | 2026-11-26 | B | Seen via mirror |
| H-39 | HF blog: MoE CPU inference with GPU acceleration in llama.cpp | https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide | 2025 | 2026-11-26 | C | |
| H-40 | DevelopersDigest: `--n-cpu-moe` vs new expert cache | https://www.developersdigest.tech/blog/llama-cpp-moe-offload-n-cpu-moe-vs-moe-cache-2026 | 2026 | 2026-11-26 | D | PR #29887 claim unverified |
| H-41 | llama.cpp RPC README | https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md | 2026 | 2026-11-26 | B | Fetched |
| H-42 | fastsafetensors repo; Yoshimura et al., arXiv 2505.23072 (IEEE CLOUD 2025) | https://github.com/foundation-model-stack/fastsafetensors | 2025–26 | 2026-11-26 | B | Fetched; claims are the authors' |
| H-43 | Model Streamer (formerly Run:ai Model Streamer) | https://github.com/run-ai/runai-model-streamer | 2026 | 2026-11-26 | B | Fetched; renamed 2026-09-13 |
| H-44 | ServerlessLLM repo; Fu et al., OSDI'24 | https://github.com/ServerlessLLM/ServerlessLLM | 2024–26 | 2026-11-26 | B | Fetched; numbers from README |
| H-45 | NVIDIA GPUDirect Storage Release Notes v1.19.1 | https://docs.nvidia.com/gpudirect-storage/release-notes/index.html | 2026-09-17 | 2026-11-26 | A | Fetch failed (DNS); via search summaries |
| H-46 | NVIDIA GDS Overview Guide r1.18 | https://docs.nvidia.com/gpudirect-storage/overview-guide/ | 2026-05-21 | 2026-11-26 | A | P2PDMA, OpenRM 570+ (snippet) |
| H-47 | NVIDIA GDS cuFile API Reference | https://docs.nvidia.com/gpudirect-storage/api-reference-guide/index.html | 2026 | 2026-11-26 | A | compat mode (snippet) |
| H-48 | NVIDIA GDS Troubleshooting Guide (CUDA 11.4 archive) | https://docs.nvidia.com/cuda/archive/11.4.0/gds/troubleshooting-guide/index.html | 2021 | 2026-11-26 | A | Tesla/Quadro only (old) |
| H-49 | NVIDIA nvidia-fs documentation v2.30 | https://docs.nvidia.com/gpudirect-storage/pdf/nvidia_fs.pdf | 2026-09-08 | 2026-11-26 | A | snippet |
| H-50 | Hershcovitch et al.: ZipNN, arXiv 2411.05239 | https://arxiv.org/abs/2411.05239 | 2024/25 | 2026-11-26 | B | snippet |
| H-51 | ZipNN follow-up for FP8/FP4, arXiv 2508.19263 | https://arxiv.org/abs/2508.19263 | 2025 | 2026-11-26 | B | snippet; ratio semantics ambiguous |
| H-52 | Hugging Face Xet docs: deduplication | https://huggingface.co/docs/xet/deduplication | n.d. | 2026-11-26 | B | snippet |
| H-53 | HF blog: Migrating the Hub from Git LFS to Xet | https://huggingface.co/blog/migrating-the-hub-to-xet | 2025 | 2026-11-26 | B | snippet |
| H-54 | NVIDIA/cuda-checkpoint | https://github.com/NVIDIA/cuda-checkpoint | n.d. | 2026-11-26 | A | Fetched (official vendor repo) |
| H-55 | LWN / Linuxiac: bcachefs removed in Linux 6.18 | https://lwn.net/Articles/1040120 | 2025 | 2026-11-26 | B | snippet; DKMS from 6.17 |
| H-56 | OpenZFS 2.3.0 release notes | https://github.com/openzfs/zfs/releases/tag/zfs-2.3.0 | 2025-01-14 | 2026-11-26 | B | Fetched; Direct IO |
| H-57 | Chen et al.: KTransformers, SOSP'25 | https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/ | 2025-10 | 2026-11-26 | A | PDF not fetched; speed-ups via secondary mirror |
| H-58 | LMSYS blog: KTransformers in SGLang | https://www.lmsys.org/blog/2025-10-22-KTransformers | 2025-10-22 | 2026-11-26 | B | snippet |
| H-59 | vLLM docs: Engine Arguments (`cpu_offload_gb`, offload backend) | https://docs.vllm.ai/en/stable/configuration/engine_args/ | 2026 | 2026-11-26 | A | snippet |
| H-60 | vLLM docs: KV Offloading Usage Guide | https://docs.vllm.ai/en/latest/features/kv_offloading_usage/ | 2026 | 2026-11-26 | A | snippet |
| H-61 | vLLM blog: KV Offloading Connector | https://vllm.ai/blog/2026-01-08-kv-offloading-connector | 2026-01-08 | 2026-11-26 | B | snippet |
| H-62 | vLLM issue #56410: `--cpu-offload-gb` uses ~1.9× host memory | https://github.com/vllm-project/vllm/issues/56410 | 2026 | 2026-11-26 | D | single report |
| H-63 | vLLM issue #57771: offload negative when weights fit (~19×) | https://github.com/vllm-project/vllm/issues/57771 | 2026 | 2026-11-26 | D | single report |
| H-64 | vLLM docs: Parallelism and Scaling | https://docs.vllm.ai/en/stable/serving/parallelism_scaling/ | 2026 | 2026-11-26 | A | snippet |
| H-65 | NVIDIA blog: Improving GPU Memory Oversubscription Performance | https://developer.nvidia.com/blog/improving-gpu-memory-oversubscription-performance/ | n.d. | 2026-11-26 | B | snippet |
| H-66 | Chien et al.: Performance Evaluation of Advanced Features in CUDA UM, arXiv 1910.09598 | https://arxiv.org/abs/1910.09598 | 2019 | 2026-11-26 | B | snippet |
| H-67 | OLCF: SummitDev Unified Memory slides | https://www.olcf.ornl.gov/wp-content/uploads/2018/02/SummitDev_Unified-Memory.pdf | 2018 | 2026-11-26 | C | fault cost ~10s µs/page |
| H-68 | exo-explore/exo | https://github.com/exo-explore/exo | n.d. | 2026-11-26 | B | Fetched |
| H-69 | b4rtaz/distributed-llama | https://github.com/b4rtaz/distributed-llama | 2026 | 2026-11-26 | B | Fetched |
| H-70 | Petals README; Borzunov et al., arXiv 2209.01188 | https://raw.githubusercontent.com/bigscience-workshop/petals/main/README.md | 2022–n.d. | 2026-11-26 | B | Status unclear |
| H-71 | Yang, Adamek, Armour: Part-time Power Measurements: nvidia-smi's Lack of Attention, arXiv 2312.02741v3 | https://arxiv.org/abs/2312.02741v3 | 2024-12-12 | 2026-11-26 | B | snippet |
| H-72 | NVIDIA Dev Forums: nvmlDeviceGetPowerUsage sampling rate | https://forums.developer.nvidia.com/t/nvmldevicegetpowerusage-sampling-rate/276517 | 2023/24 | 2026-11-26 | D | 1-s average vs instant |
| H-73 | Schöne et al.: Energy Efficiency Aspects of the AMD Zen 2 Architecture (IEEE Cluster 2021), arXiv 2108.00808 | https://arxiv.org/abs/2108.00808 | 2021 | 2026-11-26 | A | snippet |
| H-74 | Linux kernel docs: amd_energy hwmon driver (v5.12) | https://kernel.org/doc/html/v5.12/hwmon/amd_energy.html | 2021 | 2026-11-26 | A | snippet; CVE-2020-12912 |
| H-75 | LKML: [PATCH] perf/x86/rapl: Enable Core RAPL for AMD | https://lkml.rescloud.iu.edu/2302.3/04174.html | 2023 | 2026-11-26 | B | Tested Zen 2–4 servers |
| H-76 | Intel: RAPL Energy Reporting advisory (INTEL-SA-00389) | https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/running-average-power-limit-energy-reporting.html | 2020-11-10 | 2026-11-26 | A | snippet |
| H-77 | Ubuntu Security KB: PLATYPUS | https://wiki.ubuntu.com/SecurityTeam/KnowledgeBase/Platypus | 2020 | 2026-11-26 | A | chmod 400 mitigation |
| H-78 | Lipp et al.: PLATYPUS (IEEE S&P 2021) | https://misc0110.net/web/files/platypus.pdf | 2020 | 2026-11-26 | A | snippet |
| H-79 | Red Hat: CVE-2020-8694 | https://access.redhat.com/security/cve/cve-2020-8694 | 2020 | 2026-11-26 | A | snippet |
| H-80 | Khan et al.: RAPL in Action (ACM TOMPACS 2018) | https://typeset.io/pdf/rapl-in-action-experiences-in-using-rapl-for-power-29ewz97e1j.pdf | 2018 | 2026-11-26 | A | Seen in search results only |
| H-81 | CERN HEPiX: RAPL slides | https://indico.cern.ch/event/1708318/contributions/7185021/attachments/3324707/5953607/RAPL_HEPiX_Aug2026_v2.pdf | 2026-08 | 2026-11-26 | C | Milan/Rome RAPL < IPMI |
| H-82 | openai/gpt-oss | https://github.com/openai/gpt-oss | 2025 | 2026-11-26 | B | Fetched; 117B/5.1B, 21B/3.6B, MXFP4 |
| H-83 | TechPowerUp CPU DB: Core i5-11500 | https://techpowerup.com/cpu-specs/core-i5-11500.c2408 | n.d. | 2026-11-26 | C | UHD 750, dual-channel DDR4 |
| H-84 | PCI-SIG: PCIe 4.0/5.0 base specification (16/32 GT/s, 128b/130b) | https://pcisig.com/specifications | n.d. | 2026-11-26 | A | Not fetched; standard values |
| H-85 | JEDEC JESD79-5 DDR5 SDRAM standard | https://www.jedec.org/standards-documents/docs/jesd79-5 | n.d. | 2026-11-26 | A | Not fetched; 2×32-bit subchannels |
| H-86 | x86-64 psABI (micro-architecture levels v2/v3/v4) | https://gitlab.com/x86-psABIs/x86-64-ABI | n.d. | 2026-11-26 | A | Not fetched |
| H-87 | AnandTech Forums: Zen 4 core-to-core and CCD-to-CCD bandwidth | https://forums.anandtech.com/threads/zen4-core-to-core-and-ccd-to-ccd-bandwidth.2615208 | n.d. | 2026-11-26 | D | ~73 GB/s read, 2 CCDs |
| H-88 | dev.to: Hidden 146 W power draw on NVIDIA A100 GPUs | https://dev.to/mikebains41debug/how-i-discovered-a-hidden-146w-power-draw-on-nvidia-a100-gpus-and-built-an-open-source-fix-1n8h | n.d. | 2026-11-26 | D | "ghost power" anecdote |
| H-89 | Hacker News comment on Zen 4 per-CCD bandwidth (item 42239911) | https://news.ycombinator.com/item?id=42239911 | 2024 | 2026-11-26 | D | Seen via mirror; 59.7 GB/s |
| H-90 | CPU-Monkey: AMD Ryzen 9 7950X3D | https://www.cpu-monkey.com/en/cpu-amd_ryzen_9_7950x3d | n.d. | 2026-11-26 | C | "170 W PL2" |
