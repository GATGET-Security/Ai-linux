# AI Compute Engine (Phase 1.5): Capabilities, Workload Intelligence, Runtime, Autotuning

> Status: implemented (build-heavy), CPU/CI-tested, **not hardware-validated** · Last updated: 2026-11-27 · ADR-0013 (proposed)

## Summary

The compute engine (`python/src/ailx/compute/`, CLI `ailx caps | model | recommend | run | tune`)
answers four questions for local AI workloads. It changes no host setting; host knobs stay in the
transactional system layer (ADR-0011).

| Question | Component | Status |
|----------|-----------|--------|
| What can this machine do? | `capabilities.py`, `providers.py` | fixture- and host-tested; reference workstation pending (VL-1.5-01) |
| What does this model need, and where does it fit? | `gguf.py`, `model.py`, `planner.py` | exact from the file for weights and KV; buffers uncalibrated (H-R-11, VL-1.5-02) |
| How do several models run without OOM? | `runtime.py` | real llama-server tested on CPU; CUDA and 24 GB admission pending (VL-1.5-03/04) |
| Which configuration is better *here*? | `tuning.py` on the campaign stack | pipeline tested with real llama-bench (CPU) and constructed data; reference decisions pending (VL-1.5-05, H-R-12) |

Principles carried over:

- **Engine defaults are the baseline.** A run without tuning behaves exactly like stock llama.cpp.
- **Adoption needs evidence.** Only a confirmed, evidenced delta is adopted.
- **Lossy needs consent and a gate.** Lossy settings need explicit opt-in at every start plus a
  passed quality gate.
- **Experimental means opt-in.** Placement experiments are never defaults.
- **Unknown is labeled.** Unknown values stay `null` and are listed, never guessed.

## 1. Capability engine

`ailx caps` builds a capabilities document (`ailx.capabilities/v0`, ID `cap-<sha256>` over the
static part).

| Area | Content | Source |
|------|---------|--------|
| CPU | Physical cores (from `topology/core_id`), SMT, LLC domains, ISA, caches, X3D mode | sysfs, `/proc/cpuinfo` |
| NUMA | Nodes with CPUs and memory | `/sys/devices/system/node` |
| Memory | Total, swap, huge pages, THP; bandwidth `unknown` until measured or declared | `/proc/meminfo` |
| Accelerators | NVIDIA, AMD, Intel and NPU devices (see below) | `providers.py` |
| Backends | `usable` = device + driver userspace + installed engine build | combined |
| Storage | Filesystem and device of the models directory, free space (live) | `/proc/mounts`, sysfs |
| Live | Available RAM, free VRAM, load, NUMA free | excluded from the ID |

Notes on the rows:

- **CPU.** Each LLC domain (CCD) lists its CPUs, one primary CPU per core, L3 size, max clock and
  NUMA node. When the domains are asymmetric (e.g. 7950X3D), each gets a role `large-cache` or
  `frequency`. ISA is reported as x86-64 level v2–v4 plus AI extensions (AVX-512 VNNI/BF16/FP16,
  AMX, Arm dotprod/i8mm/SVE). Caches are L1d/L2 per core. X3D mode comes from `amd_x3d_vcache`.
- **Accelerators.**
  - NVIDIA: NVML → nvidia-smi → sysfs. Fields: name, VRAM, driver, CUDA driver version, compute
    capability, PCIe max gen/width, default power limit.
  - Memory bandwidth comes from the datasheet (RTX 4090: 1008 GB/s [H-12]) or is computed as
    *theoretical* `2 × max memory clock × bus width / 8`.
  - AMD (amdgpu), Intel (i915/xe) and NPUs (`/sys/class/accel`) are read from sysfs.

New device classes are a new provider function. The interface follows
`hardware-abstraction.md` (read-only part).

## 2. Workload intelligence

`ailx model inspect` reads the GGUF header with an own, bounded reader:

- no tensor data, no mmap;
- limits on tensor, KV, string and array counts;
- truncation and offset checks;
- type sizes cross-checked against `gguf-py` in the tests.

Hugging Face directories are read the same way (`config.json` + safetensors headers).

`ailx.model-analysis/v0` (ID `mdl-<sha256>`, path-independent) records:

- parameters;
- weight bytes by ggml type;
- bytes per layer, including expert tensors (`*_exps`);
- input embeddings, which llama.cpp keeps in host memory, and the output layer;
- active bytes per token (MoE: used/total experts);
- attention heads per layer (GQA);
- K/V dimensions;
- trained context.

Sliding-window and recurrent layers are flagged, because the KV estimate is then an upper bound.

`ailx model plan` (planner) computes:

| Output | How |
|--------|-----|
| Placement | 1. full GPU; 2. MoE: attention on the GPU, experts of the first *m* layers on the CPU (`--n-cpu-moe`); 3. dense: last *k* layers on the GPU; 4. CPU only; else infeasible with suggestions |
| Memory | Device and host bytes. Exact: weights, KV (`ctx × Σ heads_kv × (k+v) × bytes`). Heuristic: compute buffers, context overhead, plus the engine's 1024 MiB fit margin — labeled *uncalibrated (H-R-11)* |
| Max context | Largest context that keeps full-GPU or CPU placement feasible |
| Engine | GGUF → llama.cpp on the best *usable* backend (CUDA > ROCm > Vulkan > SYCL > CPU); safetensors → vLLM if the model fits ~90 % of VRAM (PK engine default) |
| Bottlenecks | `decode: device memory bandwidth`, `decode: host memory bandwidth … (hybrid)`, `prefill: CPU compute`, `io: … mmap` paging, `capacity` |
| Decode bound | *theoretical* `1 / (device_bytes/device_bw + host_bytes/host_bw)` at full context, only when every used bandwidth is known |
| Quality | `lossless` or `lossy (opt-in)` (KV quantization requires `--allow-lossy`) |

Notes:

- **Memory.** Free memory is respected by default; `--no-live` plans against totals.
- **Bottlenecks.** The `io` class appears when the host part exceeds available RAM, so weights
  would page from storage via mmap.

Division of labour: llama.cpp at the pinned commit already fits layers per process
(`--fit on`, `-ngl auto`). The planner predicts that result for *cross-process* admission and
produces explicit configurations for tuning; it does not override the engine by default.

## 3. Intelligent runtime

`ailx run start <name> <model>` starts llama-server as an unprivileged, separately sessioned
process with logs under `<state>/runtime`.

| Concern | Mechanism |
|---------|-----------|
| Configuration | `engine-default` (only `-c`/`-np`), `planned` (all planner flags), `tuned` (exactly the adopted delta), `auto` = tuned if a valid adoption exists, else engine defaults |
| Admission control | Predicted host/device bytes of running instances + new one ≤ budget (total − reserve; `[runtime] reserve_ram_mib/reserve_vram_mib` in `ailx.toml`) and new one ≤ free now. Refusal lists reasons and alternatives; nothing is started |
| Priorities | `interactive` / `batch` / `background` → nice 0/5/10, `oom_score_adj` 0/300/600 (only raised: no privilege). Lower classes are stopped only with `--preempt` |
| CPU placement | `none` (default) · `large-cache` · `frequency` · `llc:<id>` · `numa:<node>` · `mask:0x…` via `sched_setaffinity` on the process. All non-`none` policies are **EXPERIMENTAL** (H-H-01, H-K-09, H-K-11) |
| Lifecycle | `status` (health endpoint, RSS, threads, CPU time, VRAM per PID, RSS vs prediction), `logs`, `stop` (SIGTERM to the process group, SIGKILL after a timeout), `rm` |
| Trust | Records are untrusted (group-writable). Before signalling, PID start time and `argv` must match a binary inside the engines directory. Tuned settings are type/domain-validated; lossy values need `--allow-lossy` at each start |

Model lifecycle: `ailx model list` (models directory). Load/unload = instance start/stop. Swapping
with engine sleep/unload hooks (H-R-05) is a later step.

## 4. Autotuning foundation

`ailx tune plan | calibrate | run | verify | reset | list | show` works per (hardware profile,
engine build, model, objective `decode`|`prefill`).

1. **Candidates** are derived from the plan:
   - layer split ±2 around the predicted fit;
   - MoE expert split ±2;
   - explicit all-layers offload;
   - threads (½ physical cores, all logical CPUs) when the CPU participates;
   - flash attention on/off;
   - micro-batch for prefill;
   - KV q8_0 (**lossy**, `--allow-lossy` + quality reference);
   - CPU placements (**experimental**, `--include-experimental`).

   Predicted-infeasible candidates are dropped, and failed runs count as infeasible.
2. **`calibrate`** runs an A/A campaign on exactly the tuning workload, which yields the noise floor.
3. **Screening**: engine defaults vs all candidates (`purpose: screening`, RMIT, BH). Screening never
   adopts.
4. **Confirmation**: engine defaults vs the screening winner with fresh, independently ordered runs.
5. **Adoption** requires all of:
   - primary verdict `improvement` beyond max(e_min, 2 × noise floor);
   - no guard regression;
   - no failed run;
   - for lossy candidates, a KLD gate verdict of `no-detectable-quality-change`.

   Otherwise the engine defaults stay in force. Uncalibrated decisions say so in the record.
6. **`verify`** re-measures later. If the advantage is gone, or the engine build or hardware profile
   changed, the record is `reverted`, and `run --config auto` falls back to the engine defaults.

Records (`ailx.tuning-record/v0`) point to the campaign and analysis IDs in the result store. The
schema rejects an adoption without a confirmation analysis, and a lossy adoption without a passed
gate.

Observed on the cloud test host (4 vCPU VM, random-weight test model; mechanics only, no
performance claim; IDs refer to that host's local store, which is not published):

- An **uncalibrated** run adopted `fa-on` with a wide CI (confirmation `ana-570938dc3e56…`).
- **Calibrated** runs kept the baseline. The A/A run CV on that VM was 7–38 %
  (`ana-d6f34b2f5c40…`, `ana-26890522c745…`).

The A/A step is therefore part of the validation run, and the runbook makes it mandatory before
adoption on the reference hardware.

## 5. Hardware validation readiness

`tools/hw_validate.py` (runbook §8) runs inventory → reference expectations
(`workloads/hw/reference-7950x3d-4090.json`) → engine integrity → model plans → managed servers with
prediction ratios → admission refusal → per-model A/A calibration → calibrated tuning. It then
writes `report.json` / `report.md` with *suggested* ledger updates.

- Suggestions are suppressed on machines that deviate from the reference profile.
- `--label` names the platform (stock Ubuntu vs reference image) for the base-distribution bake-off.

## 6. Limits

- **Estimates are uncalibrated.** Buffer and overhead constants are uncalibrated (H-R-11). On the
  test host the prediction exceeded RSS ~5–7× for a 17 MB model (runtime step of the
  `hw_validate.py` mechanics run; a status reading, not a store result). That is conservative for
  admission and says nothing about large models.
- **System memory bandwidth is unknown**, so CPU and hybrid decode bounds are not computed. Fix: a
  measured value per machine (planned) or a declared one.
- **Coverage gaps.**
  - The runtime manages llama.cpp only; vLLM planning exists, but there is no managed vLLM process
    yet.
  - No cgroup limits: memory protection is admission + `oom_score_adj`, and a hard cgroup limit
    would be an opt-in later.
  - GPU sharing is cooperative: no MPS/MIG on GeForce [R-41][R-42].
- **Search is small.** The tuning search is a small candidate set (screening + confirmation), not
  Bayesian optimization; that is a Phase 2 extension on the same records.
