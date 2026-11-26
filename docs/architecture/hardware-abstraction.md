# Hardware Abstraction: Accelerator Provider Model

> Status: Phase 0 proposal (not implemented) · Last updated: 2026-11-26 · Related ADRs: 0007

## Summary

- AI Linux does **not** introduce a new compute API. Portability of *compute*
  comes from reused engines with multiple backends (ggml: CUDA/HIP/Vulkan/SYCL/
  CPU; PyTorch: CUDA/ROCm/XPU; ONNX Runtime execution providers; OpenVINO).
  See `docs/research/04-runtimes-and-workloads.md`.
- AI Linux abstracts **management**: discovery, capability description,
  driver-stack health, telemetry, controls (power, clocks), and the mapping
  "workload × hardware → runtime bundle + backend + placement".
- Each vendor/device family is a **provider plugin** behind a small, versioned
  interface. The core contains no vendor-specific code.
- Proprietary stacks (NVIDIA userspace, CUDA, TensorRT) are optional
  providers/bundles; the core and at least one open path (e.g. Vulkan/ggml,
  CPU) must work without them (ADR-0004).

## Provider interface (conceptual, v0)

```text
Provider
  id, version, vendor, device_family
  discover()            -> [DeviceDescriptor]
  capabilities(dev)     -> Capabilities
  driver_status(dev)    -> {stack: [component, version, state], issues: [...]}
  telemetry(dev)        -> stream of {ts, power_w, energy_j, util, mem_used,
                                      clocks, temp, throttle_reasons, errors}
  controls(dev)         -> [ControlDescriptor]   # e.g. power_limit, app_clocks
  apply(dev, control, value) / revert(dev, control)
  runtime_compat()      -> [(runtime_bundle, backend, constraints)]
```

`Capabilities` (normalized): memory size and theoretical bandwidth, compute
units, supported numeric formats (FP32/FP16/BF16/FP8/FP4/INT8/INT4 — native
vs emulated), host link (PCIe gen/width, measured bandwidth), unified/shared
memory, P2P support, partitioning features (MIG/SR-IOV/none), preemption
granularity if known, power-control ranges, NPU-specific limits (ops/model
formats).

Rules:

1. Capability values carry provenance: `datasheet`, `driver-reported`, or
   `measured:<result-id>`. The planner prefers measured values.
2. Controls declare reversibility and safety class like any knob (see
   `self-optimization.md`). Example: GPU power-limit reduction is
   runtime-reversible and *allowed*; over-volting/over-clocking is
   *explicit-opt-in* (stability risk) and never auto-proposed.
3. Providers must work read-only without root; controls go through `ailxd`.

## Planned providers

| Provider | Devices | Kernel side | Userspace/compute | Notes for Phase 1–4 |
|----------|---------|-------------|-------------------|---------------------|
| `cpu-x86` | AMD Zen, Intel Core/Xeon | cpufreq (amd-pstate / intel_pstate), RAPL, resctrl, sched_ext | ggml CPU, oneDNN, OpenVINO CPU, ONNX Runtime | Must model asymmetric CCDs (e.g. X3D V-Cache CCD) and ISA (AVX-512/VNNI/BF16) |
| `nvidia` | GeForce/RTX/data-center | NVIDIA open kernel modules (preferred; Turing+, must match proprietary userspace/GSP firmware version) [D-59][D-60] | NVML, CUDA, cuBLAS, optional TensorRT | First target (RTX 4090). Optional/proprietary userspace; GeForce has no MIG; MPS status unclear [R-41][R-42] |
| `amd-gpu` | Radeon/Instinct | amdgpu/amdkfd (upstream) | ROCm/HIP (ROCm 10, TheRock-built) [R-33][R-34], amd-smi, Vulkan (RADV) | Consumer support varies by generation; llama.cpp Vulkan reportedly beats ROCm on several consumer setups [R-74] (H-R-07) |
| `intel-gpu` | Arc/iGPU (Xe) | xe / i915 | Level Zero, upstream PyTorch XPU, OpenVINO, Vulkan (ANV); IPEX/IPEX-LLM archived [L-27][L-28] | iGPU of the i5-11500 test server is a low-priority secondary target (support contested [H-27][H-28]) |
| `amd-npu` | Ryzen AI XDNA | amdxdna (accel) | Lemonade/FastFlowLM, Ryzen AI SW, ONNX Runtime EP [R-37] | No test hardware yet (neither test CPU has an NPU) |
| `intel-npu` | Core Ultra NPU | ivpu (accel) | OpenVINO NPU plugin, linux-npu-driver [R-50] | No test hardware yet |
| `vulkan-generic` | any Vulkan 1.3 device | vendor DRM driver | ggml-vulkan etc. | Portable fallback; also a baseline for comparing vendor stacks |
| `future-accel` | e.g. Tenstorrent, other accel-subsystem devices | DRM accel subsystem | vendor SDK | Interface designed so that new device classes need no core change |

Kernel and driver facts behind this table: `docs/research/02-kernel-and-os.md`;
runtime/backend support matrix: `docs/research/04-runtimes-and-workloads.md`.

## Placement planner (target design)

Input: workload manifest (model, format, context length, latency/throughput
goal, quality constraints), hardware profile, current resource usage.

Output: runtime bundle + backend + device placement (e.g. layer/expert split
between VRAM and system RAM) + initial knob set, with predicted resource use.

Steps:

1. **Feasibility** — filter bundles by format support and memory fit (weights +
   KV cache + activations + runtime overhead). Fit estimates are calibrated
   against measurements; uncalibrated estimates are labeled as such.
2. **Lossless first** — prefer configurations that keep the model's requested
   numeric format. If nothing fits, present lossy alternatives (smaller quant,
   KV-cache quantization) with measured quality delta, and require opt-in.
3. **Rank** by predicted objective (from results store / community priors).
4. **Hand over** to the self-optimizer for refinement on the actual machine.

## Multi-vendor and multi-device rules

- One machine may host several providers (e.g. NVIDIA dGPU + Intel iGPU + CPU).
  Placement may split *workloads* across devices; splitting a *single model*
  across vendors is only supported where the engine supports it
  (e.g. ggml multi-backend), and is treated as experimental.
- Driver installation is delegated to the base distribution's packages or the
  vendor's official repositories. AI Linux verifies and reports; it does not
  re-package proprietary drivers unless licensing allows and maintainers
  decide so (see `docs/research/05-base-distributions-and-licensing.md`).
- Secure Boot: out-of-tree modules must be signed (distribution or MOK flow);
  disabling Secure Boot is never a default (ADR-0004).

## Open design questions

- Device-level preemption and VRAM oversubscription differ greatly by vendor
  and driver; the orchestrator's GPU-sharing policy must degrade gracefully
  (see `docs/state/open-questions.md`).
- How to represent NPUs whose supported operator sets/model formats are narrow
  (capability = list of supported model families/compilers rather than FLOPs).
- Whether to adopt an existing device/telemetry abstraction (e.g. Kubernetes
  DRA-style device descriptions, OpenTelemetry semantic conventions) instead
  of defining our own schema.
