# Linux Kernel and Low-Level OS Optimization for Local AI Workloads

> Status: Phase 0 research · Last updated: 2026-11-26 · Evidence grading: see 00-method.md

## Summary

- **Kernel baseline.** Mainline is at 7.4-rc4 [K-01]. 7.3 is the newest stable series (released about 2026-10-18; 7.3.5 is current) [K-02][K-03]. 6.18 is the newest long-term kernel and 6.12 LTS is still maintained [K-03][K-04]. Some production `scx` schedulers need ≥ 6.16 [K-14], and several features below arrived in 7.x. AI Linux should therefore follow a recent stable kernel and not stay on 6.12.
- **Cache-aware load balancing (`CONFIG_SCHED_CACHE`) landed in 7.2 and defaults to on for every multi-LLC CPU** [K-19][K-20][K-21]. It pulls a process's threads into one LLC (one CCD on Ryzen). On single-node desktops the footprint check is skipped, so only the thread count limits it [K-20]. Nobody has measured it on AI workloads. This may be the most consequential untested default for multi-CCD CPU inference (H-K-01).
- **sched_ext is mature enough to ship as opt-in.** It can be attached and detached at runtime. If it fails, the kernel aborts it and moves tasks back to the fair class (error, SysRq-S, or a runnable task starved for up to 30 s) [K-08][K-09]. Since 7.3, cgroup **sub-schedulers** allow per-workload policies [K-09][K-17]. `scx_layered`, `scx_lavd` and `scx_rusty` call themselves production-ready [K-12][K-13][K-16]. `scx_cosmos --gpu` already places NVIDIA GPU processes near their GPU [K-15]. Meta runs `scx_layered` on a GPU training fleet [K-18].
- **Preemption.** Since 2026-01 the build options on x86 are only `PREEMPT`, `PREEMPT_LAZY` (the default) and `PREEMPT_RT`. `PREEMPT_NONE` and `PREEMPT_VOLUNTARY` are now limited to other architectures [K-05][K-06]. PREEMPT_LAZY is the sensible default. Nothing shows that PREEMPT_RT helps AI throughput.
- **The GPU-side kernel interfaces are fragmented.** The `dmem` cgroup controller caps device memory per region; the docs use xe as the example [K-28]. Reclaim for dmem is still under review [K-47]. drm_sched now has an experimental FAIR policy, but FIFO is the default [K-48]. The NVIDIA stack (open modules 620.x, Turing+) is out of tree [K-38]. Nova is still "work in progress and may not be functional" [K-44][K-45].
- **Memory.** Per-size mTHP controls, 1 GiB hugetlbfs, MGLRU thrash protection (`min_ttl_ms`), zswap, DAMON actions (`hugepage`, `collapse`, `migrate_hot/cold`) and weighted interleave all exist in mainline [K-29][K-32][K-33][K-34][K-35][K-36]. HMM-based shared virtual memory is available for NVIDIA (UVM, open modules) and AMD (KFD SVM) [K-37][K-38][K-39]. All of these are tuning and policy jobs. None needs new kernel code.
- **Security boundaries are concrete.** x86 already defaults to lazy IOMMU invalidation [K-41]. Attack-vector controls (since about 6.17) let us turn off only mitigations that cannot apply, for example guest-to-host on a machine with no VMs [K-56]. Under Secure Boot lockdown (integrity mode), writable debugfs knobs such as `sched/llc_balancing/*` and MSR writes are blocked [K-61]. Tuning must therefore go through sysfs, sysctl and cgroup interfaces.
- **Kernel build optimizations probably add little.** AutoFDO and Propeller are in mainline. The authors claim low single-digit gains on server and network benchmarks [K-52][K-53]. The kernel is built with `-mno-sse -mno-avx` [K-54], so x86-64-v3/v4 targets barely affect kernel code. No evidence exists for GPU-bound AI workloads.
- **Observability is mostly in place.** PSI (with unprivileged triggers in 2 s windows) [K-51], DRM fdinfo per-client engine and memory statistics [K-49], `scx` stats [K-11] and perf/bpftrace are available. RAPL energy is readable by root only [K-50], so a privileged exporter is needed.
- **Research to track:** `gpu_ext` (eBPF hooks inside the NVIDIA open GPU driver for eviction, prefetch and scheduling; the preprint claims up to 4.8× throughput) [K-59]. Other directions are joint CPU/GPU scheduling through `scx_cosmos`-style GPU awareness and dmem reclaim.

## Scope

This document covers kernel and low-level OS mechanisms that affect local AI workloads: CPU scheduling, NUMA and caches, memory management, device I/O, accelerator drivers, power, kernel builds, security constraints and observability. Each finding is tagged **[kernel]** (needs or uses in-kernel mechanisms) or **[userspace]** (daemons, configuration and tools that drive kernel interfaces).

The following are **out of scope** here: model-loading and storage detail (see 03-hardware-io-and-model-loading.md), inference runtimes (04), and distribution choice (05).

No experiments were run (see 00-method.md). Version facts were checked against the mainline tree on GitHub (master at 7.4-rc4, as of 2026-11-26) wherever possible.

## Findings

### 1. CPU scheduling

**1.1 EEVDF fair class [kernel].** EEVDF replaced CFS, starting with 6.6 [K-07]. It picks the eligible task (lag ≥ 0) with the earliest virtual deadline. Tasks can ask for a custom time slice through `sched_setattr()`, which favours latency-sensitive threads without RT priority [K-07]. For AI work this is the cheapest lever. A runtime can give its GPU-launch or token-sampling thread a short slice and give data-loader workers a long one. (This use is our inference from the mechanism, not a measured result.)

**1.2 sched_ext [kernel + userspace].**
- *Safety model.* A BPF scheduler can be turned on and off at any time. Any error, the program exiting, SysRq-S, or a stalled runnable task aborts it, and all tasks return to the fair class [K-08].
- *Watchdog.* `timeout_ms` is the longest a runnable task may wait. It defaults to 30 s, which is also the maximum [K-09].
- *Partial switching.* With `SCX_OPS_SWITCH_PARTIAL`, only tasks that use the `SCHED_EXT` policy go to the BPF scheduler [K-08].
- *Sub-schedulers.* A scheduler can be attached to a cgroup (`sub_cgroup_id`) [K-09]. `CONFIG_EXT_SUB_SCHED` defaults to y [K-19]. Press reports call the work "feature complete" in 7.3 [K-17]. 7.3 also reportedly added a `cgroup_mode` for CPU-weight distribution [K-60] (not verified against source).
- *Kernel requirements.* `CONFIG_SCHED_CLASS_EXT` needs BPF_SYSCALL, BPF_JIT and DEBUG_INFO_BTF [K-05]. sched_ext has been upstream since 6.12 [K-10].

**1.3 scx schedulers (checked against the tree on 2026-11-26) [userspace].**
- *Main schedulers* [K-11]: `scx_bpfland`, `scx_cake`, `scx_chaos`, `scx_cosmos`, `scx_eevdf`, `scx_lavd`, `scx_layered`, `scx_mitosis`, `scx_pandemonium`, `scx_p2dq`, `scx_rusty`.
- *Experimental:* `scx_flow`, `scx_nitosis`, `scx_mavd`, `scx_mlfq`, `scx_panda`, `scx_reevdf`, `scx_rburst`, `scx_rlocal`, `scx_rvtime`.
- *`scx_flash` is not in the current workspace* [K-11]. Its README returns 404, so it was probably removed or renamed. Its status is unverified.
- *Production-ready by their own READMEs:*
  - `scx_layered`: user-defined layers with CPU guarantees [K-12].
  - `scx_lavd`: latency-criticality-aware virtual deadlines with per-LLC and per-core-type domains. It was built for interactive and gaming workloads [K-13].
  - `scx_rusty`: per-LLC domains, best on multi-CCX single-socket machines [K-16].
- *Other relevant behaviour:*
  - `scx_bpfland` is EEVDF-like and lets waking tasks borrow virtual time. It needs Linux ≥ 6.16 [K-14].
  - `scx_p2dq` uses pick-two load balancing across LLCs and NUMA nodes [K-16].
  - `scx_mitosis` isolates cgroups into cells of CPUs [K-16].
  - **`scx_cosmos --gpu`** finds NVIDIA GPU processes through NVML and prefers CPUs on the GPU's NUMA node. It treats the process's cgroup as the workload boundary [K-15].
- *Fleet evidence.* Meta deployed `scx_layered` across a Reality Labs GPU training fleet. Their reason: the frequent cross-process synchronization in training is sensitive to small scheduling delays [K-18]. This is a talk; we have seen no quantitative data.

**1.4 What AI threads need [analysis].** AI workloads mix four kinds of threads:
- GPU feeder and kernel-launch threads: short bursts on the critical path.
- Tokenizer, sampler and detokenizer threads: short, latency-sensitive.
- Data-loader and decode workers: throughput batch work.
- Interactive desktop and agent tool processes.

When CPUs are free, any reasonable scheduler does well. The differences show up under contention, for example compiling or running agent tools next to a serving process. In that case the options are EEVDF slices (1.1), `scx_lavd`, or `scx_layered` with explicit layers (H-K-02, H-K-03). We found no public measurements that compare these options on inference latency.

**1.5 Preemption models [kernel].**
- *Current state.* A 2026-01-08 commit ("sched: Further restrict the preemption modes") limits the build choices on lazy-capable architectures such as x86 to `PREEMPT`, `PREEMPT_LAZY` and `PREEMPT_RT` [K-05][K-06]. `PREEMPT_LAZY` is the default when the architecture supports it [K-05].
- *Lazy behaviour.* Under lazy preemption a task gets one HZ tick to yield before it is preempted [K-26], so HZ matters again for it.
- *PREEMPT_RT* is a mainline option under `EXPERT && ARCH_SUPPORTS_RT` [K-05]. It was merged in the 6.12 cycle (background knowledge, not re-verified here).
- *Boot-time switching.* Since 2026-09, `PREEMPT_DYNAMIC` depends on `ARCH_HAS_PREEMPT_LAZY` [K-06]. Yet `preempt=` still documents `none` and `voluntary` [K-26]; see "Contradictory or weak evidence".

**1.6 Isolation and IRQs [kernel].**
- `isolcpus=` domain isolation is fixed at boot. The kernel docs recommend cpusets for dynamic setups [K-26]. Its `nohz` flag equals `nohz_full`, and `managed_irq` keeps managed IRQs off isolated CPUs [K-26].
- cgroup v2 `cpuset.cpus.partition=isolated` creates isolated partitions at runtime with no load balancing [K-28].
- The kernel's CPU-isolation guide targets extreme cases such as DPDK [K-27]. We expect little benefit for GPU-bound inference (H-K-08).

**1.7 Cache-aware scheduling [kernel].**
- *What it does.* `CONFIG_SCHED_CACHE` ("Cache aware load balance", default y) "will attempt to aggregate tasks from the same process onto a single LLC" [K-19]. It was merged for 7.2 [K-21], building on LKML series v3 and v4 (2026-02 to 2026-04) [K-22].
- *Runtime gate.* It switches on only when the topology has several LLCs. It can be toggled at `/sys/kernel/debug/sched/llc_balancing/enabled`. Other tunables there are `aggr_tolerance`, `imb_pct`, `overaggr_pct` and the epoch settings [K-20].
- *Heuristics* [K-20]:
  - Aggregation is skipped if the process's sampled footprint exceeds the LLC size times a tolerance. The footprint comes from NUMA-balancing fault statistics, so the check only applies when NUMA balancing is active.
  - Aggregation is also skipped if the average running threads × SMT width reach about 80 % of the LLC's CPUs.
  - On a **single-node desktop** (NUMA balancing inactive), only the thread-count rule applies.
- *RDT interaction.* A source comment marks interaction with RDT-reserved cache ways as "TBD" [K-20].
- *Lockdown.* Because the toggle lives in debugfs, it cannot be written under lockdown in integrity mode [K-61].

**1.8 cpufreq and CPU topology hints [kernel].**
- *amd-pstate modes.* `active` mode (EPP) takes EPP values 0–255 or named profiles. A newer `dynamic` EPP mode follows the platform profile and AC/battery state [K-24]. `passive` and `guided` modes also exist.
- *Preferred cores.* amd-pstate exposes preferred-core rankings (`amd_pstate_hw_prefcore`, `amd_pstate_prefcore_ranking`), which the scheduler uses. `amd_prefcore=disable` turns this off [K-24].
- *intel_pstate.* HWP EPP offers the equivalent controls [K-25].
- *AMD X3D.* The `amd_x3d_vcache` platform driver (since 6.13) switches core ranking between `frequency` (the faster CCD first) and `cache` (the large-L3 CCD first). The switch is at `/sys/bus/platform/drivers/amd_x3d_vcache/AMDI0101:00/amd_x3d_mode` [K-23]. Using it per workload is cheap (H-K-09).

**1.9 Core scheduling [kernel].** `CONFIG_SCHED_CORE` (off by default; `prctl(PR_SCHED_CORE)`) makes SMT siblings run only mutually trusted tasks [K-05]. It is an alternative to `nosmt` for isolating untrusted agent sandboxes. It costs some throughput but keeps SMT for trusted work. sched_ext implements core-sched support too [K-06].

### 2. NUMA and cache locality

- **[kernel]** Automatic NUMA balancing (`numa_balancing` = 1 normal, 2 memory tiering) samples memory accesses by unmapping pages and catching the resulting faults. The kernel docs say to disable it when a workload is already bound to nodes [K-30]. Tiering mode moves hot pages to fast memory, with a promotion rate limit [K-30].
- **[kernel]** `MPOL_WEIGHTED_INTERLEAVE` spreads pages across nodes by the weights set in `/sys/kernel/mm/mempolicy/weighted_interleave/` [K-29]. This fits bandwidth-bound CPU inference across DRAM and CXL nodes, or across sockets.
- **[kernel]** On single-socket Zen 4/5, several CCDs or L3 domains share one NUMA node. LLC-aware scx schedulers (`scx_rusty`, `scx_lavd`, `scx_p2dq`) model these domains explicitly [K-13][K-16], and now so does the fair class (§1.7).
- **[kernel]** resctrl supports Intel RDT and "AMD Platform QoS": L3 CAT, MBM, MBA, SMBA (slow-memory/CXL bandwidth allocation), ABMC counters, and `io_alloc` (reserving L3 for I/O cache injection on AMD L3 SDCI) [K-31]. Cache and bandwidth partitioning could protect an interactive or serving tenant from a batch tenant. On client parts, hardware support varies and has to be probed.

### 3. Memory

- **[kernel] THP and mTHP.** Each THP size can be set to `always`, `madvise`, `never` or `inherit` under `/sys/kernel/mm/transparent_hugepage/hugepages-<size>kB/`. khugepaged collapses file and shmem memory only to PMD-size pages; anonymous memory can collapse to the other mTHP sizes [K-32]. Defrag policy (`defer+madvise` and others) controls how much stall time page faults may take [K-32].
- **[kernel] hugetlbfs.** Supports 2 MiB pages and, where the CPU allows, 1 GiB pages. 1 GiB pages are reliable only when reserved at boot [K-33]. They suit pinned host staging buffers and KV-cache offload pools where memory is planned up front.
- **[kernel] Reclaim.** MGLRU (`CONFIG_LRU_GEN`) has a `min_ttl_ms` setting that protects the working set from thrashing, which the docs recommend for desktops [K-34]. zswap trades CPU for less swap I/O [K-35]. memcg `memory.min`, `memory.low` and `memory.high` protect the inference service's page cache (mmapped weights) from agent and tool memory spikes [K-28].
- **[kernel] DAMON.** DAMOS actions include `hugepage` and `collapse` (madvise-driven THP), `lru_prio`, `pageout` and `migrate_hot`/`migrate_cold` (tiering) [K-36]. With these, a userspace policy can promote hot weight or KV regions without app changes (H-K-05).
- **[kernel] HMM and GPU shared virtual memory.** HMM provides `hmm_range_fault` (mirroring CPU page tables) and `migrate_vma_*` (moving pages into device-private memory) [K-37].
  - *NVIDIA:* UVM enables HMM "if possible" (module parameter `uvm_disable_hmm`). It stays off under Confidential Computing [K-38]. HMM ships in the open modules.
  - *AMD:* KFD offers `HSA_AMD_SVM`, an HMM-based SVM for HIP unified and managed memory (default y) [K-39].
  - *Caveat:* migration happens through page faults, so HMM is a convenience path for oversubscription. It is not a fast path (H-K-06).
- **[analysis] Pinned memory cost.** Pinned (page-locked) host buffers cannot be reclaimed, compacted or swapped. Large pinned pools therefore shrink what THP, compaction and the page cache have to work with. These pools should be sized explicitly and charged to the owning cgroup. (This comes from general MM design; we did not measure it.)

### 4. Device and I/O (brief; see 03)

- **[kernel] DMA-BUF** is the shared-buffer framework between drivers. `udmabuf` turns memfd regions into dma-bufs (off by default) [K-62].
- **[kernel] PCI P2PDMA** always allows peer-to-peer transfers inside a PCIe switch hierarchy. It blocks routing through the host bridge unless the platform is on an allowlist [K-40]. Direct NVMe-to-GPU transfers therefore depend on the platform.
- **[kernel] IOMMU** [K-41][K-26]:
  - *Strict:* the IOMMU TLB is invalidated synchronously at every unmap.
  - *Lazy:* invalidations are batched. A device may reach unmapped memory "for some time". **Lazy is the x86 default.**
  - *Passthrough:* trusted devices get an identity mapping, i.e. unrestricted memory access.
  - Devices marked untrusted always get strict mode with bounce buffering.
  - The GPU's long-lived mappings make IOMMU overhead mostly a one-time setup cost (H-K-04).
- **[kernel] io_uring** zero-copy receive takes network payload straight into user memory, but needs NIC header/data split [K-42]. Registered buffers, O_DIRECT and NVMe `uring_cmd` passthrough are covered in 03.

### 5. Accelerator drivers in the kernel

- **[kernel] accel subsystem drivers:** `amdxdna` (AMD XDNA NPU in client APUs), `ivpu` (Intel NPU, Meteor Lake and newer), `qaic`, `habanalabs`, `rocket` and `ethosu` [K-43].
- **[kernel] DRM drivers:** `amdgpu`/`amdkfd`, `xe`, `i915`, `nouveau` and the Rust `nova` (nova-core plus nova-drm) [K-44]. Both nova Kconfig entries say "work in progress and may not be functional" [K-44]. At LPC 2026, nova booted GSP firmware on Turing through Blackwell with basic functions only, and it is not ready for end users [K-45].
- **[kernel, out-of-tree] NVIDIA open GPU kernel modules** (620.73.04, Turing or newer, dual MIT/GPL) need matching GSP firmware and the proprietary userspace [K-38]. NVIDIA made the open modules the default from R560 and requires them for Blackwell and Grace Hopper [K-46]. Out-of-tree modules mean DKMS and module signing are needed (§8).
- **[kernel] drm_sched** has a `sched_policy` module parameter: RR, FIFO (default) or **FAIR (experimental)** [K-48]. This is the first in-kernel move toward fair multi-client GPU job scheduling.
- **[kernel] dmem cgroup.** `dmem.max`, `dmem.min`, `dmem.low`, `dmem.current` and `dmem.peak` per region, plus `dmem.capacity` at the root. The docs give xe VRAM examples [K-28]. Status:
  - An LKML series adding **reclaim** to dmem (amdgpu, TTM, xe) reached v6 in 2026-06; merge status unverified [K-47].
  - NVIDIA's MPS v3 docs (seen only as a search summary) say dmem is used on kernels ≥ 6.14 [K-47]. How NVIDIA integrates with it is unclear.
  - We found no GPU *compute*-time cgroup in mainline.

### 6. Power and energy

- **[kernel] Energy readings.** powercap/RAPL `energy_uj` is readable by root only (`S_IRUSR`) [K-50]. Background (not re-verified): the restriction answers the RAPL power side channel (Platypus, CVE-2020-8694). Do not loosen it; export coarse values through a privileged daemon (§8).
- **[kernel] EPP.** `energy_performance_preference` exists for amd-pstate and intel_pstate [K-24][K-25]. A per-profile EPP (performance while serving, balanced when idle) is safe and reversible.
- **[kernel] PSI.** Cumulative stall times per resource (`cpu`, `memory`, `io`, and per cgroup) with poll()-able triggers. Unprivileged triggers must use window sizes that are multiples of 2 s [K-51]. This supports admission control, for example deferring a model load or agent spawn while memory pressure is high.

### 7. Kernel build optimizations

- **[kernel] PGO.** AutoFDO and Propeller builds (Clang; Propeller needs LLVM ≥ 19 and is best stacked on AutoFDO plus ThinLTO) are documented in mainline [K-52]. The authors claim, on 6.9.x, Neper +6.1 % throughput, −10.6 % latency and UnixBench +2.2/2.6 % [K-53]. These are network and syscall-heavy benchmarks, not AI.
- **[kernel] ISA targets.** `X86_NATIVE_CPU` (`-march=native`) exists [K-54]. The x86 kernel is built with `-mno-sse -mno-mmx -mno-sse2 -mno-avx` [K-54], so x86-64-v3/v4 mostly affects scalar code generation inside the kernel. Userspace v3/v4 results are mixed: vendor claims of 5–20 % against independent tests that show small or even negative effects [K-55].
- **[analysis]** No source shows measurable kernel-build gains for GPU-bound inference. We expect the effect to fall within run-to-run noise there. Syscall- and I/O-heavy phases (model loading, agent tool execution) are the only plausible beneficiaries (H-K-07).

### 8. Security constraints [kernel + userspace]

- **Attack-vector controls** (first documented 2025-07-11, about the 6.17 cycle) cover five vectors: user-to-kernel, user-to-user, guest-to-host, guest-to-guest and cross-thread (SMT). The kernel tells admins to disable only the vectors that do not apply [K-56]. It says user-to-kernel mitigations matter when "unknown userspace applications may be run". Autonomous agents running generated code are exactly that case, so user-to-kernel and user-to-user stay on.
- **Lockdown classes** [K-61][K-26]:
  - *Integrity mode* blocks unsigned modules, `/dev/mem`, MSR writes, unsafe module parameters, writable debugfs files and BPF writes to user memory.
  - *Confidentiality mode* additionally blocks kprobes, BPF kernel reads, perf and tracefs. bpftrace and many profilers stop working in this mode.
- **Module signing.** `module.sig_enforce=1` or `CONFIG_MODULE_SIG_FORCE` rejects unsigned modules [K-57]. DKMS signs modules with a MOK key and certificate that must be enrolled in firmware for Secure Boot [K-58]. This is the supported route for NVIDIA open modules.
- **Unprivileged access.** `perf_event_paranoid` defaults to 2, and CAP_PERFMON is the recommended privilege. `unprivileged_bpf_disabled` can be set to 1 (irreversible) or 2 [K-30]. sched_ext and bpftrace need CAP_BPF or root, so access should be granted per daemon rather than system-wide.

### 9. Observability

- **[kernel] GPU telemetry.** DRM fdinfo (`drm-engine-*` ns, `drm-cycles-*`, `drm-total-cycles-*`, `drm-maxfreq-*`, memory keys) gives standard per-client GPU usage across DRM drivers [K-49]. dmem gives per-cgroup VRAM usage (`dmem.current`, `dmem.peak`) [K-28]. NVIDIA telemetry goes through NVML in userspace, which `scx_cosmos` already uses [K-15].
- **[userspace] Tools.** perf, bpftrace and `scx` monitors (`--monitor` statistics) [K-11]. The sched_ext dump tracepoint and SysRq-D capture scheduler state [K-08]. PSI [K-51] and RAPL through a privileged exporter [K-50].

### 10. Experimental ideas worth tracking

- **gpu_ext** (arXiv 2512.12615, 2025-12) adds about 100 lines of hooks to the NVIDIA open modules' UVM code. eBPF policies there control eviction (2 MB blocks), prefetch and TSG scheduling (priority, timeslice, interleave). A device-side eBPF-to-PTX path is included. The authors claim up to 4.8× throughput on inference, training and vector search [K-59]. This is a preprint with no independent replication.
- **CPU/GPU co-scheduling.** `scx_cosmos --gpu` uses NVML locality [K-15]. A vendor-neutral version could read DRM fdinfo, so the CPU scheduler would know which processes are GPU-active (O-K-01).
- **GPU job fairness:** the drm_sched FAIR policy [K-48]. **Device memory QoS:** dmem reclaim [K-47]. Combined with sched_ext sub-schedulers, these could give per-agent CPU and GPU isolation built only from open components (H-K-10).

## Optimization opportunities

| ID | Level | Opportunity | Expected benefit | Feasibility | Risk | Effort | Evidence | Novelty |
|----|-------|-------------|------------------|-------------|------|--------|----------|---------|
| O-K-01 | userspace | GPU-aware CPU placement daemon: detect GPU-active processes (DRM fdinfo, NVML) and set cpuset or scx hints toward the GPU-local node or CCD | M: lower feeder jitter on multi-CCD/NUMA hosts; L on single-CCD | High | Low (hints only) | M | [K-15][K-49] | known-elsewhere (scx_cosmos, NVIDIA only) |
| O-K-02 | userspace | Ship vetted `scx` profiles (e.g. `scx_lavd` desktop+AI, `scx_layered` serving+batch) as opt-in, with automatic fallback | M under contention; ~0 when CPUs are idle | High | Low (kernel aborts to fair class) | M | [K-08][K-12][K-13][K-18] | established |
| O-K-03 | runtime | Runtimes request EEVDF custom slices (`sched_setattr`) for launch/sampler threads; long slices for loaders | L–M: tail latency under load | High | Low | S | [K-07] | known-elsewhere |
| O-K-04 | kernel/userspace | Benchmark `SCHED_CACHE` on AI workloads; ship a policy (sysctl/cgroup, not debugfs) if it regresses | M: avoids a possible silent regression on multi-CCD CPU inference | Medium (knob is debugfs-only) | Medium | M | [K-19][K-20][K-61] | novel (evaluation) |
| O-K-05 | userspace | Memory profile: THP `madvise` + mTHP sizes for runtimes, MGLRU `min_ttl_ms`, zswap, `memory.min` for the inference service, `memory.high` for agents | M: fewer stalls and thrash when agents and models share RAM | High | Low | S | [K-28][K-32][K-34][K-35] | established |
| O-K-06 | userspace | Weighted-interleave profiles for bandwidth-bound CPU inference on multi-node or CXL systems | M on multi-node; none on UMA | Medium | Low | S | [K-29][K-30] | known-elsewhere |
| O-K-07 | userspace | systemd slices with `dmem.max`/`dmem.min` per service or agent (amdgpu/xe first) | M: VRAM isolation between agents | Medium (driver support varies) | Medium (eviction thrash) | M | [K-28][K-47] | novel (integration) |
| O-K-08 | userspace | PSI-triggered admission control: delay model loads or agent spawns under pressure | M: stability and fewer OOMs | High | Low | S | [K-51] | known-elsewhere (oomd-style) |
| O-K-09 | userspace | Unified telemetry: fdinfo + dmem + PSI + RAPL (privileged, coarse) + sched_ext stats, per process | H for tuning and self-optimization (06) | High | Low (if RAPL stays privileged) | M | [K-49][K-50][K-51] | known-elsewhere |
| O-K-10 | kernel | Clang ThinLTO + AutoFDO/Propeller kernel with profiles from AI loading and agent workloads | L (no AI evidence) | Medium | Medium (toolchain, reproducibility) | L | [K-52][K-53] | established |
| O-K-11 | userspace | X3D mode and EPP switching per workload profile (cache vs frequency) | L–M, workload-dependent | High | Low | S | [K-23][K-24] | known-elsewhere |
| O-K-12 | userspace | Dynamic CPU isolation through cpuset `isolated` partitions for latency-critical serving | L for GPU-bound work | High | Low | S | [K-26][K-28] | established |
| O-K-13 | userspace | resctrl L3 and bandwidth partitioning between interactive/serving and batch tenants | L–M on supported CPUs | Low–Medium (client HW support) | Low | M | [K-31] | known-elsewhere |
| O-K-14 | userspace | Core scheduling for agent sandboxes instead of global `nosmt` | Keeps SMT throughput while isolating untrusted code | Medium | Medium (perf overhead) | M | [K-05] | known-elsewhere |
| O-K-15 | kernel | Track and prototype eBPF GPU policy hooks (gpu_ext) and drm_sched FAIR for multi-tenant GPUs | Potentially H, unproven | Low (out-of-tree, research) | High | XL | [K-48][K-59] | novel |

## Security classification of tuning knobs

| Knob | Effect | Security impact | Policy |
|------|--------|-----------------|--------|
| `mitigations=off` | Disables all CPU-vulnerability mitigations | Exposes kernel and other processes to transient-execution leaks; agents run untrusted code | **forbidden** (default and profiles) |
| Attack-vector controls: disable `guest_host`/`guest_guest` with no VMs | Drops mitigations for vectors that cannot occur | None while no VMs run [K-56] | **opt-in** (auto-revert if KVM is used) |
| Disable `user_kernel`/`user_user`/`smt` vectors | Drops mitigations that matter for untrusted code | High on agent systems [K-56] | **forbidden** by default; expert opt-in only on air-gapped benchmark boxes |
| `iommu=off`, `amd_iommu=off`, `intel_iommu=off` | No DMA remapping | Any device can DMA anywhere; breaks VFIO isolation | **forbidden** |
| `iommu.passthrough=1` (`iommu=pt`) | Identity map for trusted devices | Unrestricted DMA by internal devices [K-41] | **opt-in** (documented; measured need only) |
| `iommu.strict=0` (lazy) | Batched IOTLB invalidation | Short post-unmap window [K-41] | **allowed** (x86 default) |
| `nokaslr` | Disables KASLR | Easier kernel exploitation; no performance benefit | **forbidden** (debug kernels only) |
| Turning off Secure Boot or the distro's lockdown | Allows unsigned code, MSR and debugfs writes | Kernel integrity is lost [K-61] | **opt-in** (developer mode, visible warning) |
| Disabling SELinux/AppArmor | Removes MAC confinement | Large, especially for agents | **forbidden** |
| `module.sig_enforce=0`, unsigned DKMS modules | Loads unsigned modules | Rootkit vector | **forbidden**; sign through DKMS + MOK [K-58] |
| `kernel.perf_event_paranoid=-1` | All users get all perf events | Side channels, info leaks | **opt-in**; prefer CAP_PERFMON on a helper (**allowed**) [K-30] |
| `kernel.unprivileged_bpf_disabled=0` | Unprivileged BPF | Large verifier attack surface | **forbidden**; grant CAP_BPF per daemon [K-30] |
| Loading project-signed `scx` schedulers (root) | Replaces CPU scheduling policy | Bounded by verifier, watchdog and fallback [K-08] | **allowed** (opt-in profile) |
| World-readable RAPL `energy_uj` | Unprivileged energy readings | Power side channel (background: CVE-2020-8694) | **forbidden**; coarse privileged exporter **allowed** |
| `nosmt` / core scheduling | Disables or partitions SMT | Improves isolation | **allowed** |
| THP/mTHP, hugetlb, MGLRU, zswap, memcg limits | Memory policy | None (memcg *reduces* DoS risk) | **allowed** |
| Raising `RLIMIT_MEMLOCK` / large pinned pools | Allows pinning | Local DoS via unreclaimable memory | **allowed** per service with memcg limits |
| `preempt=`, EPP, governor, X3D mode, isolcpus/nohz_full, IRQ affinity, resctrl, `numa_balancing` | Performance policy | None or negligible | **allowed** |

## Hypotheses

**Hypothesis H-K-01: SCHED_CACHE can reduce CPU-inference throughput on multi-CCD desktops.**
- *Statement:* On single-node multi-CCD CPUs (e.g. 2-CCD Ryzen), cache-aware scheduling can hurt memory-bandwidth-bound token generation when the thread count is below about 80 % of one CCD's CPUs. Packing threads into one CCD limits them to that CCD's fabric bandwidth.
- *Rationale:* The footprint check is inactive without NUMA balancing [K-20], and LLM weights are far larger than any L3.
- *Test:* llama.cpp CPU decode at 4–12 threads with `llc_balancing/enabled` = 0 vs 1. Record tokens/s and per-CCD placement (perf sched).
- *Risk:* If confirmed, the new default silently costs performance. If refuted, a tuning knob can be dropped.

**Hypothesis H-K-02: sched_ext choice matters only under CPU contention.**
- *Statement:* For GPU-bound serving, EEVDF and `scx_*` show no significant difference in inter-token latency (ITL) or time-to-first-token (TTFT) p99 while CPUs are idle. Under concurrent compile or agent load, `scx_lavd` or `scx_layered` reduce p99.
- *Rationale:* Feeder threads are short and latency-critical; contention is what creates queueing [K-13][K-18].
- *Test:* llama.cpp server or vLLM with a background `make -j` and agent tool loops, across EEVDF / bpfland / lavd / layered; ≥ 10 runs, CIs.
- *Risk:* Shipping a custom scheduler for no gain adds maintenance burden.

**Hypothesis H-K-03: EEVDF slice hints recover most of the scx benefit.**
- *Statement:* Per-thread `sched_setattr` slice requests recover most of any sched_ext latency benefit without a BPF scheduler.
- *Rationale:* EEVDF already orders tasks by virtual deadline, and short slices give earlier deadlines [K-07].
- *Test:* Same as H-K-02, with runtime-set slices on stock EEVDF.
- *Risk:* Low. Runtimes need patches, or an LD_PRELOAD/daemon helper.

**Hypothesis H-K-04: IOMMU mode barely affects AI workloads.**
- *Statement:* Strict vs lazy vs passthrough changes model-load throughput and steady-state GPU inference by less than run-to-run noise on a single GPU. P2P and multi-GPU collectives are the exception.
- *Rationale:* GPU buffers are mapped once and used for a long time. Lazy mode already batches invalidations [K-41].
- *Test:* Model load (O_DIRECT and mmap) and GPU H2D bandwidth under each mode, with IOMMU perf counters.
- *Risk:* Passthrough might be justified for multi-GPU P2P, which would need an opt-in.

**Hypothesis H-K-05: DAMON-driven THP collapse speeds up CPU inference without app changes.**
- *Statement:* Using DAMON-driven `collapse`/`hugepage` on hot anonymous weight regions (`--no-mmap` loads) lowers dTLB misses and raises CPU-inference tokens/s without app changes.
- *Rationale:* The DAMOS actions exist [K-36], and khugepaged's anonymous mTHP collapse is limited [K-32].
- *Test:* perf `dTLB-load-misses` and tokens/s with THP never / madvise / always / DAMON policy.
- *Risk:* Memory bloat and compaction stalls.

**Hypothesis H-K-06: HMM oversubscription is slower than explicit offload.**
- *Statement:* Running models that exceed VRAM through HMM/UVM fault-driven migration is slower than explicit layer offload in the runtime.
- *Rationale:* Fault handling and migration go page by page or block by block [K-37][K-38]. gpu_ext's prefetch hooks exist because the default policies are generic [K-59].
- *Test:* The same model with runtime offload vs `cudaMallocManaged`/system-allocated memory.
- *Risk:* Low. It only informs runtime defaults.

**Hypothesis H-K-07: Kernel PGO/LTO gains are lost in noise for AI work.**
- *Statement:* An AutoFDO+ThinLTO kernel gives no statistically significant gain for GPU-bound inference. It may give a small gain for model loading and agent tool execution.
- *Rationale:* The published gains come from network and syscall benchmarks [K-53]. Inference time is spent in userspace and on the GPU.
- *Test:* A/B kernels on fixed hardware with the 06 benchmark harness.
- *Risk:* Effort is spent on a reproducible PGO toolchain for no benefit.

**Hypothesis H-K-08: Static isolation does not help single-user GPU inference.**
- *Statement:* `isolcpus`/`nohz_full` give no throughput benefit for single-user GPU inference. They help only jitter-sensitive multi-tenant serving.
- *Rationale:* The kernel's guide targets DPDK-class workloads [K-27].
- *Test:* ITL p99.9 with and without isolation under background noise.
- *Risk:* Static isolation wastes cores on desktops.

**Hypothesis H-K-09: X3D "cache" mode helps small models but not large-model decode.**
- *Statement:* X3D `cache` mode helps CPU-side work whose working set fits in L3 (tokenizers, small embedding models). It does not help decode of models > 1 GB.
- *Rationale:* Weights stream from DRAM; only small working sets benefit from extra L3 [K-23].
- *Test:* An embedding-model benchmark and llama.cpp decode under each mode.
- *Risk:* Low.

**Hypothesis H-K-10: Open-stack multi-agent isolation is feasible on amdgpu/xe.**
- *Statement:* sched_ext sub-schedulers plus dmem limits plus drm_sched FAIR can isolate concurrent agents on CPU and GPU using only open components on amdgpu/xe.
- *Rationale:* The building blocks are in mainline or under review [K-09][K-28][K-47][K-48].
- *Test:* A prototype with two agents and one interactive session; measure interference.
- *Risk:* FAIR is experimental, dmem reclaim may not merge, and NVIDIA support is unclear.

## Contradictory or weak evidence

- **Cache-aware scheduling merge status.** A 2026-07 cover letter claimed the work was in mainline while it was still on a `sched/cache` branch in 2026-05 [K-22]. It was later reported merged for 7.2 [K-21], and master has `CONFIG_SCHED_CACHE` default y [K-19]. The status is resolved. However, the press-reported gains (e.g. "up to 360 % MySQL" from a follow-up series) were seen only through an aggregator and are unverified [K-21]. No AI data exists.
- **Preemption options vs boot docs.** The build options exclude NONE and VOLUNTARY on x86 [K-05], yet the `preempt=` documentation still lists `none` and `voluntary` [K-26]. Whether runtime selection of those modes still works on x86 is unclear.
- **EEVDF doc wording.** The kernel doc says the transition began "in version 6.6 (as a new option in 2024)" [K-07]. 6.6 was released in 2023, so the wording is internally inconsistent.
- **Size of AutoFDO gains.** The cover letter (2–6 %, specific benchmarks) and a talk ("up to 20 %", as reported) disagree [K-53]. Neither covers AI.
- **x86-64-v3 gains.** Vendor claims of 5–20 % against independent tests showing mixed results, sometimes slower [K-55].
- **7.3 release date.** Phoronix says 2026-10-18; a later aggregator article is dated 2026-10-29 [K-02][K-60]. We assume publication lag.
- **NVIDIA and dmem.** The MPS docs say dmem is used [K-47], but the dmem reclaim series names no NVIDIA driver. The integration path is unclear.
- **gpu_ext 4.8×.** A single-group preprint claim with no independent replication [K-59].
- **scx_flash.** It was named in the brief but is absent from the current scx workspace [K-11].

## Open questions

1. What does `SCHED_CACHE` do to llama.cpp/ggml CPU inference and to multi-process serving (vLLM workers) on 2-CCD Ryzen and Threadripper? Will a non-debugfs knob (sysctl or per-process prctl) be accepted upstream?
2. Is dmem reclaim merged (7.3 or 7.4)? Do NVIDIA's out-of-tree modules register dmem regions, and with what semantics?
3. Which kernel will be the 2026 LTS? (kernel.org was unreachable; the last 2026 release is the usual candidate.)
4. Does the drm_sched FAIR policy affect amdgpu/xe AI throughput or latency? Is there a plan to make it the default?
5. Under distro Secure Boot (lockdown integrity), which `scx` loaders, bpftrace scripts and power tools still work? Which of them need confidentiality-mode exemptions?
6. How large is the real cost of HMM/UVM oversubscription compared with runtime offload on consumer GPUs?
7. Can amdxdna and ivpu NPUs be scheduled and accounted (fdinfo, dmem, cgroups) alongside GPUs for agent workloads?
8. Is there evidence that PREEMPT_RT helps or hurts audio/voice pipelines (ASR/TTS) running next to LLM inference?

## Access limitations

- `www.kernel.org`, `lwn.net`, `phoronix.com`, `lpc.events`, `arxiv.org` and `developer.nvidia.com` failed DNS resolution from the sandbox. We did not retry them. Their content was seen only as **WebSearch summaries**: K-02, K-04, K-17, K-18, K-21, K-45, K-46, K-47 (MPS part), K-55, K-59 and K-60.
- `api.github.com` returned 403 (repository access not enabled for this session), so no code search or directory listings were possible.
- `raw.githubusercontent.com` (Linux master, sched-ext/scx, NVIDIA open-gpu-kernel-modules, dell/dkms) **worked through curl** in this session. This differs from the general note in 00-method.md. Most grade-A findings come from these raw files.
- One `github.com` page fetch returned 503 once (`scx/scheds/rust/README.md`); the raw file was used instead.
- The **WebSearch budget ran out mid-research** (shared per-turn limit). The following were therefore not re-verified this session and are marked as background knowledge or left without versions: CUDA HMM kernel-version requirements, the merge versions of MPOL_WEIGHTED_INTERLEAVE / io_uring zcrx / PREEMPT_RT / amdxdna, nouveau GSP defaults, and the RAPL CVE background.

## Sources

| ID | Title | URL | Date | Accessed | Grade | Notes |
|----|-------|-----|------|----------|-------|-------|
| K-01 | Linux top-level Makefile (master) | https://github.com/torvalds/linux/blob/master/Makefile | n.d. (7.4-rc4) | 2026-11-26 | A | VERSION 7, PATCHLEVEL 4, -rc4 |
| K-02 | Phoronix: Linux 7.3 Released / 7.3 features | https://phoronix.com/news/Linux-7.3-Released | 2026-10-18 | 2026-11-26 | C | Search summary only (host blocked) |
| K-03 | Linux stable 7.3.5, 6.18.64, 6.12.118 | https://www.linuxcompatible.org/story/linux-stable-735-61864-and-612118-kernels-ship-today-with-1863-combined-fixes/ | n.d. (2026-11) | 2026-11-26 | C | Search summary |
| K-04 | The Register: Linux 6.18 … likely next LTS | https://www.theregister.com/2025/12/03/kernel_version_618/ | 2025-12-03 | 2026-11-26 | C | Search summary |
| K-05 | kernel/Kconfig.preempt (+ SCHED_CORE, SCHED_CLASS_EXT) | https://github.com/torvalds/linux/blob/master/kernel/Kconfig.preempt | n.d. (master) | 2026-11-26 | A | Raw file read |
| K-06 | Commit history of kernel/Kconfig.preempt | https://github.com/torvalds/linux/commits/master/kernel/Kconfig.preempt | 2024-11-05 … 2026-09-28 | 2026-11-26 | A | Lazy model 2024-11-05; restriction 2026-01-08 |
| K-07 | Documentation/scheduler/sched-eevdf.rst | https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-eevdf.rst | n.d. | 2026-11-26 | A | Custom slices via sched_setattr |
| K-08 | Documentation/scheduler/sched-ext.rst | https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-ext.rst | n.d. | 2026-11-26 | A | Safety, partial switch, SysRq |
| K-09 | kernel/sched/ext/internal.h | https://github.com/torvalds/linux/blob/master/kernel/sched/ext/internal.h | n.d. | 2026-11-26 | A | timeout_ms ≤ 30 s; sub_cgroup_id |
| K-10 | sched-ext/scx README | https://github.com/sched-ext/scx | n.d. | 2026-11-26 | B | Upstream since 6.12; Meta/Google |
| K-11 | scx scheds/rust/README.md and Cargo.toml | https://github.com/sched-ext/scx/tree/main/scheds/rust | n.d. | 2026-11-26 | B | Scheduler list; scx_flash 404 |
| K-12 | scx_layered README | https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_layered | n.d. | 2026-11-26 | B | "Production Ready? Yes" |
| K-13 | scx_lavd README | https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_lavd | n.d. | 2026-11-26 | B | Latency-criticality; per-LLC domains |
| K-14 | scx_bpfland README | https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_bpfland | n.d. | 2026-11-26 | B | Requires ≥ 6.16 |
| K-15 | scx_cosmos README | https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_cosmos | n.d. | 2026-11-26 | B | `--gpu` NVML locality |
| K-16 | scx_p2dq / scx_rusty / scx_mitosis READMEs | https://github.com/sched-ext/scx/tree/main/scheds/rust | n.d. | 2026-11-26 | B | LLC/NUMA balancing; cells |
| K-17 | Phoronix: sched_ext sub-scheduler "feature complete" with 7.3 | https://phoronix.com/news/Linux-7.3-sched-ext | 2026-08-22 | 2026-11-26 | C | Search summary only |
| K-18 | LPC 2025: Accelerating AI training fleets with sched_ext (Meta) | https://lpc.events/event/19/contributions/2039/ | 2025-12-12 | 2026-11-26 | C | Talk; search summary only |
| K-19 | init/Kconfig (SCHED_CACHE, EXT_SUB_SCHED, NUMA_BALANCING) | https://github.com/torvalds/linux/blob/master/init/Kconfig | n.d. | 2026-11-26 | A | SCHED_CACHE default y |
| K-20 | kernel/sched/{fair.c,topology.c,debug.c} | https://github.com/torvalds/linux/tree/master/kernel/sched | n.d. | 2026-11-26 | A | LLC heuristics, multi-LLC gate, debugfs |
| K-21 | Phoronix/It's FOSS: cache-aware scheduling for 7.2 | https://phoronix.com/news/linux-7.2-likely-cas | 2026-05/06 | 2026-11-26 | C | Search summaries; gains unverified |
| K-22 | LKML: [Patch v4 00/22] Cache aware scheduling | https://lkml.iu.edu/2604.0/02001.html | 2026-04-01 | 2026-11-26 | B | Search summary; v3 2026-02 |
| K-23 | amd x3d_vcache driver + sysfs ABI doc | https://github.com/torvalds/linux/blob/master/drivers/platform/x86/amd/x3d_vcache.c | 2024-11 (6.13) | 2026-11-26 | A | frequency/cache modes |
| K-24 | Documentation/admin-guide/pm/amd-pstate.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/pm/amd-pstate.rst | n.d. | 2026-11-26 | A | EPP, dynamic EPP, prefcore |
| K-25 | Documentation/admin-guide/pm/intel_pstate.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/pm/intel_pstate.rst | n.d. | 2026-11-26 | A | HWP/EPP |
| K-26 | Documentation/admin-guide/kernel-parameters.txt | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/kernel-parameters.txt | n.d. | 2026-11-26 | A | isolcpus, iommu.*, preempt=, lockdown= |
| K-27 | Documentation/admin-guide/cpu-isolation.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cpu-isolation.rst | n.d. | 2026-11-26 | A | Housekeeping, target workloads |
| K-28 | Documentation/admin-guide/cgroup-v2.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cgroup-v2.rst | n.d. | 2026-11-26 | A | cpuset partitions, memcg, dmem |
| K-29 | Documentation/admin-guide/mm/numa_memory_policy.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/numa_memory_policy.rst | n.d. | 2026-11-26 | A | MPOL_WEIGHTED_INTERLEAVE |
| K-30 | Documentation/admin-guide/sysctl/kernel.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/sysctl/kernel.rst | n.d. | 2026-11-26 | A | numa_balancing, perf, BPF sysctls |
| K-31 | Documentation/filesystems/resctrl.rst | https://github.com/torvalds/linux/blob/master/Documentation/filesystems/resctrl.rst | n.d. | 2026-11-26 | A | AMD QoS, SMBA, ABMC, io_alloc |
| K-32 | Documentation/admin-guide/mm/transhuge.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/transhuge.rst | n.d. | 2026-11-26 | A | mTHP, khugepaged limits |
| K-33 | Documentation/admin-guide/mm/hugetlbpage.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/hugetlbpage.rst | n.d. | 2026-11-26 | A | 1 GiB pages at boot |
| K-34 | Documentation/admin-guide/mm/multigen_lru.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/multigen_lru.rst | n.d. | 2026-11-26 | A | min_ttl_ms |
| K-35 | Documentation/admin-guide/mm/zswap.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/zswap.rst | n.d. | 2026-11-26 | A | Trade-offs |
| K-36 | Documentation/mm/damon/design.rst + admin-guide/mm/damon/usage.rst | https://github.com/torvalds/linux/blob/master/Documentation/mm/damon/design.rst | n.d. | 2026-11-26 | A | DAMOS actions |
| K-37 | Documentation/mm/hmm.rst | https://github.com/torvalds/linux/blob/master/Documentation/mm/hmm.rst | n.d. | 2026-11-26 | A | hmm_range_fault, migrate_vma |
| K-38 | NVIDIA open-gpu-kernel-modules README + nvidia-uvm/uvm_hmm.c | https://github.com/NVIDIA/open-gpu-kernel-modules | n.d. (620.73.04) | 2026-11-26 | A | Turing+; HMM enable conditions |
| K-39 | drivers/gpu/drm/amd/amdkfd/Kconfig | https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/amdkfd/Kconfig | n.d. | 2026-11-26 | A | HSA_AMD_SVM (HMM) |
| K-40 | Documentation/driver-api/pci/p2pdma.rst | https://github.com/torvalds/linux/blob/master/Documentation/driver-api/pci/p2pdma.rst | n.d. | 2026-11-26 | A | Host-bridge allowlist |
| K-41 | drivers/iommu/Kconfig | https://github.com/torvalds/linux/blob/master/drivers/iommu/Kconfig | n.d. | 2026-11-26 | A | Lazy default on x86 |
| K-42 | Documentation/networking/iou-zcrx.rst | https://github.com/torvalds/linux/blob/master/Documentation/networking/iou-zcrx.rst | n.d. | 2026-11-26 | A | io_uring ZC Rx |
| K-43 | drivers/accel/Kconfig, accel docs, ivpu Kconfig, amdxdna doc | https://github.com/torvalds/linux/tree/master/drivers/accel | n.d. | 2026-11-26 | A | Accel driver list |
| K-44 | drivers/gpu/Makefile, nova-core and drm/nova Kconfig | https://github.com/torvalds/linux/tree/master/drivers/gpu/nova-core | n.d. | 2026-11-26 | A | "work in progress" |
| K-45 | Phoronix: Nova driver progress at LPC 2026 | https://www.phoronix.com/news/Nova-Driver-LPC-2026 | 2026-10 | 2026-11-26 | C | Search summary only |
| K-46 | NVIDIA blog: transitions fully towards open-source GPU kernel modules | https://developer.nvidia.com/blog/nvidia-transitions-fully-towards-open-source-gpu-kernel-modules | 2024-07-17 | 2026-11-26 | B | Search summary only |
| K-47 | LKML: [PATCH v6] Add reclaim to the dmem cgroup controller; NVIDIA MPS v3 memory partitioning | https://lkml.rescloud.iu.edu/hypermail/linux/kernel/2606.1/09601.html | 2026-06 | 2026-11-26 | B | Search summaries; merge unverified |
| K-48 | include/drm/gpu_scheduler.h + scheduler/sched_main.c | https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/scheduler/sched_main.c | n.d. | 2026-11-26 | A | FIFO default; FAIR experimental |
| K-49 | Documentation/gpu/drm-usage-stats.rst | https://github.com/torvalds/linux/blob/master/Documentation/gpu/drm-usage-stats.rst | n.d. | 2026-11-26 | A | fdinfo keys |
| K-50 | drivers/powercap/powercap_sys.c | https://github.com/torvalds/linux/blob/master/drivers/powercap/powercap_sys.c | n.d. | 2026-11-26 | A | energy_uj S_IRUSR |
| K-51 | Documentation/accounting/psi.rst | https://github.com/torvalds/linux/blob/master/Documentation/accounting/psi.rst | n.d. | 2026-11-26 | A | Triggers, unprivileged 2 s windows |
| K-52 | Documentation/dev-tools/autofdo.rst + propeller.rst | https://github.com/torvalds/linux/blob/master/Documentation/dev-tools/autofdo.rst | n.d. | 2026-11-26 | A | Clang PGO for kernel |
| K-53 | [PATCH v7 0/7] Add AutoFDO and Propeller support for Clang build (R. Xu) | https://lkml.iu.edu/hypermail/linux/kernel/2411.0/01852.html | 2024-11-02 | 2026-11-26 | B | Authors' benchmark claims |
| K-54 | arch/x86/Kconfig.cpu + arch/x86/Makefile | https://github.com/torvalds/linux/blob/master/arch/x86/Makefile | n.d. | 2026-11-26 | A | X86_NATIVE_CPU; -mno-avx |
| K-55 | Phoronix CachyOS x86-64-v3/v4 benchmarks; sunnyflunk "x86-64-v3: Mixed Bag" | https://phoronix.com/review/cachyos-x86-64-v3-v4 | 2024-02-29; 2023-01-15 | 2026-11-26 | C | Search summaries |
| K-56 | Documentation/admin-guide/hw-vuln/attack_vector_controls.rst (+ commit history) | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/hw-vuln/attack_vector_controls.rst | 2025-07-11 | 2026-11-26 | A | Five vectors |
| K-57 | Documentation/admin-guide/module-signing.rst | https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/module-signing.rst | n.d. | 2026-11-26 | A | sig_enforce |
| K-58 | dell/dkms README (Secure Boot, MOK) | https://github.com/dell/dkms | n.d. | 2026-11-26 | B | MOK signing |
| K-59 | gpu_ext: Extensible OS Policies for GPUs via eBPF (arXiv 2512.12615) | https://arxiv.org/abs/2512.12615 | 2025-12 (v2 2025-12-20) | 2026-11-26 | B | Preprint; search summary only |
| K-60 | OSTechnix / LinuxToday: Linux 7.3 released | https://ostechnix.com/linux-kernel-7-3-released/ | 2026-10 | 2026-11-26 | C | sub-schedulers, cgroup_mode |
| K-61 | include/linux/security.h (lockdown_reason) + fs/debugfs/file.c | https://github.com/torvalds/linux/blob/master/include/linux/security.h | n.d. | 2026-11-26 | A | Integrity vs confidentiality classes |
| K-62 | drivers/dma-buf/Kconfig (UDMABUF) | https://github.com/torvalds/linux/blob/master/drivers/dma-buf/Kconfig | n.d. | 2026-11-26 | A | memfd → dma-buf |
