# Phase 1.1 — Measurement Foundation: Development Specification

> Status: Ready for implementation once the prerequisites in §1 are met · Last updated: 2026-11-26 · Related: `benchmarking.md`, `hardware-test-plan.md`, ADR-0003, ADR-0006, ADR-0008, ADR-0010

## Summary

- **Goal**: make *one* trustworthy, reproducible statement about *one* change
  on *one* machine — with confidence interval, quality check, energy and full
  provenance — and prove the harness itself is trustworthy (A/A, known-effect
  injection) before any hardware campaign.
- **Language policy**: Python for the benchmark infrastructure (orchestration,
  adapters, statistics, storage, reports). **Contracts are language-neutral**:
  canonical JSON Schemas and a line-based sampler protocol, so performance-
  critical parts (samplers, privileged helpers, later the daemon) can be
  written in any language without changing the Python side.
- **Not in scope**: tuning, knob application, `ailxd`, images, schedulers,
  GPU sharing. Phase 1.1 only *measures*.
- **Size**: 8 work packages, roughly 6 weeks for 1–2 engineers (planning
  estimate). Everything is testable in CPU-only CI; hardware enters in Block 1.2.

## 1. Prerequisites (blocking)

| # | Prerequisite | Owner | Why |
|---|-------------|-------|-----|
| P1 | Licence decision (ADR-0009) — or an explicit maintainer statement that code may be committed under the proposed set pending review | Maintainers | No code without a licence |
| P2 | ADR-0003/0004 unchanged; ADR-0006 and ADR-0010 accepted (with review amendments) | Maintainers | Baseline and validation states are used by the code |
| P3 | Python ≥ 3.12 toolchain allowed in CI (GitHub Actions, CPU-only runners) | Maintainers | CI |

Non-blocking for 1.1 but needed for 1.2: wall meter procurement (A4), access to
the workstation and the Proxmox server.

## 2. Repository layout (to create)

```
schemas/                         # canonical, language-neutral contracts (JSON Schema 2020-12)
  hardware-profile.v0.schema.json
  environment-fingerprint.v0.schema.json
  workload.v0.schema.json
  campaign.v0.schema.json        # pre-registered analysis plan + arms + RMIT design
  result.v0.schema.json
  sample-stream.v0.schema.json   # sampler line protocol
  knob.v0.schema.json            # draft only (used from Phase 2)
  fixtures/{valid,invalid}/...   # conformance corpus for every schema
python/
  pyproject.toml                 # package "ailx-bench"; tools: uv, ruff, mypy (strict), pytest, hypothesis
  src/ailx/
    probe/        # hardware profile + environment fingerprint
    workloads/    # manifest loading, synthetic workload
    adapters/     # llama_bench.py, vllm_bench.py, guidellm.py (subprocess + parsers)
    telemetry/    # sampler supervisor; nvml, psi, rapl-client, meter adapters
    stats/        # rmit design, bootstrap, effect sizes, tests, FDR, power, A/A
    quality/      # llama.cpp KLD gate
    store/        # content-addressed result store + SQLite index
    report/       # report generator (cites result IDs only)
    cli.py        # `ailx-bench` entry point
  tests/
helpers/
  rapl-collector/                # minimal privileged helper (any language; reference impl. may be Python)
workloads/
  smoke/                         # v0 manifests: synthetic, llama-bench tiny model, CPU-only
docs/                            # existing
```

Rules: SPDX header in every file (per ADR-0009); no network access at runtime
except explicit model fetch commands; no root required except the RAPL helper.

## 3. Language-neutral contracts

**JSON Schemas are canonical.** Python models (pydantic v2 or dataclasses) must
pass the same conformance fixtures; CI fails if any valid fixture is rejected or
any invalid fixture accepted. Canonical serialization for hashing: RFC 8785 JSON
Canonicalization Scheme; IDs = `sha256` of canonical bytes, prefixed with the
type (`res-`, `hwp-`, `env-`, `wkl-`, `cmp-`).

**Sampler protocol (`sample-stream.v0`)** — any executable that writes JSON
Lines to stdout:

```json
{"schema":"ailx.sample-stream/v0","kind":"header","sampler":"nvml","version":"0.1.0","clock":"CLOCK_MONOTONIC","metrics":[{"name":"gpu0.energy_mj","unit":"mJ","type":"counter"}]}
{"kind":"sample","t_ns":123456789,"values":{"gpu0.energy_mj":1234567}}
{"kind":"event","t_ns":123456999,"name":"throttle","attrs":{"reason":"thermal"}}
```

- Timestamps: `CLOCK_MONOTONIC` nanoseconds; the supervisor records one
  `CLOCK_REALTIME`↔`CLOCK_MONOTONIC` pair per run for alignment.
- Counters are preferred over instantaneous values (energy counters rather than
  sampled power) [S-29].
- The supervisor starts/stops samplers, tags phases (warmup, measure, cooldown)
  and fails the run if a sampler dies.

## 4. Work packages

### WP1 — Schemas and conformance corpus (≈4 days)

Deliver all schemas in §2 with fixtures. Minimum fields:

| Schema | Required fields (abridged) |
|--------|---------------------------|
| hardware-profile | cpu{vendor, model, sockets, logical_cpus, smt, llc_domains[{id, cpus, size_bytes}], isa_flags[]}, memory{total_bytes, numa_nodes}, accelerators[{vendor, model, arch, memory_domains[{kind: discrete\|unified\|host, size_bytes, bw_theoretical?}], pcie{gen,width}?, driver}], storage[{model, transport, link?}], power_interfaces[], provenance per value (`datasheet`/`driver`/`measured:<res-id>`) |
| environment-fingerprint | kernel{release, cmdline, config_hash?}, os{id, version_id}, firmware{bios, microcode}, drivers[], security{secure_boot, lockdown, mitigations{}, iommu}, sched{ext_state, sched_cache?, preempt}, cpufreq{driver, governor, epp}, x3d_mode?, thp{enabled, defrag}, services{tuning_daemons[]}, runtime_bundles[{name, digest}], models[{path, sha256}], harness{version, git_sha} |
| workload | id, version, type, runtime{adapter, bundle_digest\|binary}, model{ref, sha256}, params{}, load{pattern, concurrency?, dataset_sha256?, output_len_policy: fixed\|natural}, metrics[], quality{gate, reference?}, warmup{iterations}, duration{min_s, max_runs, rsd_stop} |
| campaign | id, hypothesis_ids[], arms[{id, description, setup_ref}], design{type: rmit, blocks, seed}, primary_metric, guard_metrics[], e_min, alpha, fdr: none\|bh, stopping_rule — **frozen before the first run (pre-registration)** |
| result | id, campaign_id, arm_id, block, order_index, workload_id, fingerprint_id, hardware_profile_id, raw_samples_ref, summary{}, quality{}, stability_events[], energy{}, status |

**Acceptance**: `pytest schemas` validates 100% of fixtures as expected;
schemas documented in `schemas/README.md`. Note for WP1: compare the
accelerator part with CDI/DRA-style descriptions before freezing v0 (review T8).

### WP2 — `ailx-probe` (≈5 days)

Collect read-only, unprivileged where possible:

| Area | Sources |
|------|---------|
| CPU/LLC | `/proc/cpuinfo`, `/sys/devices/system/cpu/cpu*/cache/index3/{size,shared_cpu_list}`, `/sys/devices/system/cpu/smt/active` |
| Memory/NUMA | `/proc/meminfo`, `/sys/devices/system/node/` |
| GPUs | NVML (`nvidia-ml-py`, optional), `/sys/class/drm/card*/device/*`, `lspci -vv` link status and resizable BAR (parse; root optional) |
| Storage | `/sys/block/*/{device/model,queue/*}` |
| Kernel/security | `uname`, `/proc/cmdline`, `/sys/kernel/security/lockdown`, `/sys/devices/system/cpu/vulnerabilities/*`, Secure Boot state via efivars, `/sys/class/iommu/` |
| Scheduling | `/sys/kernel/sched_ext/state` (if present), preempt mode (`/sys/kernel/debug/sched/preempt` only if readable), cpufreq driver/governor/EPP, `amd_x3d_vcache` mode |
| Memory policy | `/sys/kernel/mm/transparent_hugepage/*`, `vm.*` selected sysctls |
| Services | running tuning daemons (TuneD, power-profiles-daemon, irqbalance, thermald) via systemd |
| Artifacts | model file sha256 (cached by `(dev, inode, size, mtime)`), container image digests |

Unavailable fields are recorded as `null` with a `reason`, never guessed.

**Acceptance**: produces schema-valid profile + fingerprint on the CI runner and
on a developer laptop; golden-file tests from captured sysfs trees (fixtures of a
7950X3D-like 2-LLC topology and a single-LLC topology); identical system →
identical fingerprint ID across two invocations.

### WP3 — Workload runner and adapters (≈6 days)

- Runner executes a workload manifest as subprocess with phases (warmup,
  measure, cooldown), timeouts, environment capture and stdout/stderr logs.
- Adapters (subprocess + parser; versions recorded):
  - `llama_bench`: invoke with JSON output, parse pp/tg rates per repetition.
  - `vllm_bench`: `vllm bench serve` with saved result JSON; map to pinned
    metric definitions (`benchmarking.md` §3).
  - `guidellm`: JSON report → pinned metrics.
- **Synthetic workload** `synthetic-cpu`: deterministic CPU/memory kernel with a
  controllable injected effect (e.g. `--slowdown 1.05`) and controllable noise —
  the harness's own test subject.
- Output-length policy enforced: `fixed` workloads must request fixed lengths
  (EOS ignored) and the adapter verifies achieved lengths.

**Acceptance**: parsers tested against recorded fixture outputs (per tool
version); synthetic workload runs end-to-end in CI; failure modes (crash,
timeout, OOM exit code) map to result `status` without losing samples.

### WP4 — Statistics (≈6 days)

Functions (pure, typed, documented with references):

| Function | Method | Reference |
|----------|--------|-----------|
| `rmit_schedule(arms, blocks, seed)` | Randomized multiple interleaved trials; seed recorded | [S-50] |
| `bootstrap_ci(x, stat, n=10000, method="bca")` | BCa bootstrap CI of median/mean/percentiles | [S-49] |
| `effect_size(a, b)` | Ratio of medians with bootstrap CI; Hodges–Lehmann shift | [S-48][S-49] |
| `mann_whitney(a, b)` | Nonparametric test (screening) | [S-49] |
| `fdr_bh(pvalues, q)` | Benjamini–Hochberg for screening campaigns | review §5 |
| `rsd_stop(samples, threshold, max_runs)` | PTS-style dynamic run count | [S-56] |
| `power_n(cv, e_min, alpha, power)` | Required runs from A/A variance (simulation-based) | review §5 |
| `decide(campaign, results)` | Applies the pre-registered rule: claim iff CI lower bound > max(E_min, 2×noise floor) | `benchmarking.md` §4 |

**Acceptance (simulation tests in CI)**:
- A/A: over ≥ 1000 simulated campaigns (log-normal noise, CoV 1–5%, drift and
  periodic interference variants), the false-claim rate is ≤ α + 0.02.
- Known effect: a 5% injected effect at CoV 2% is detected with power ≥ 0.8
  at the sample size returned by `power_n`.
- Interleaving: with injected periodic interference, RMIT ordering keeps the
  false-claim rate within bound while sequential AAA…BBB ordering exceeds it
  (demonstrates why RMIT is required).

### WP5 — Telemetry (≈6 days)

| Sampler | Implementation | Notes |
|---------|---------------|-------|
| `nvml` | Python (`nvidia-ml-py`), optional | Total energy counter, instant and average power, clocks, temperature, memory used, throttle reasons; ≥ 10 Hz |
| `psi` | Python | `/proc/pressure/{cpu,memory,io}` |
| `cpu` | Python | per-CPU utilization, frequency; optional perf counters if permitted |
| `rapl-client` | Python client to `helpers/rapl-collector` | Helper runs as root (systemd unit), reads powercap `energy_uj`, exposes **aggregated, rate-limited** counters on a group-restricted Unix socket; never changes file permissions [K-50][H-76] |
| `meter` | Adapter interface + `fake` + `csv-import` | Real meter driver once A4 is decided |
| `amdsmi` | Stub with interface only | Phase 4 |

**Acceptance**: samplers emit valid `sample-stream.v0`; supervisor aligns
phases; NVML sampler degrades gracefully on hosts without NVIDIA; RAPL helper
has a threat-model note and a test that it refuses non-group clients.

### WP6 — Quality gate v0 (≈4 days)

- Wrap llama.cpp perplexity tooling: create reference logits
  (`--kl-divergence-base`) for a reference model/precision on a fixed corpus;
  evaluate candidates with `--kl-divergence`; parse mean KLD (with
  uncertainty), same-top-p, Δp percentiles [S-62].
- Store references content-addressed (they are large; path + sha256 in the
  store, not inside result records).
- Gate decision: pass iff candidate KLD and same-top-p are within the A/A
  distribution at the same precision (thresholds computed, not hard-coded).

**Acceptance**: end-to-end on a tiny open model in CI (CPU); a deliberately
lossy variant (lower-bit quantization of the same tiny model) fails the gate;
an identical re-run passes.

### WP7 — Result store and reports (≈4 days)

- Store: immutable result records (canonical JSON, zstd-compressed raw samples)
  in a content-addressed directory; SQLite index for queries; export to
  Parquet optional.
- Report generator: Markdown/HTML report per campaign with arms, design,
  pre-registered plan, CIs, effect sizes, quality outcomes, stability events,
  energy, fingerprint diffs between arms; **every number links a result ID**.
- `ailx-bench validate-claim <text>` helper: rejects numbers without result IDs
  (used later in CI for docs).

**Acceptance**: round-trip tests; tampering with a stored record is detected
(hash mismatch); report generated from the synthetic campaign in CI.

### WP8 — CLI, CI and runbook (≈3 days)

- CLI: `ailx-bench probe | run <workload> | campaign plan|run|analyze | report | store verify`.
- CI: lint, type-check, unit + simulation tests, schema conformance,
  synthetic end-to-end campaign, tiny-model KLD gate; SBOM of the Python
  environment.
- Runbook for Block 1.2: HW-0…HW-2 steps from `hardware-test-plan.md` mapped to
  CLI commands.

## 5. Definition of Done (Block 1.1)

1. All WP acceptance criteria green in CI.
2. A synthetic A/A campaign and a synthetic 5%-effect campaign produce the
   expected decisions with reports that cite result IDs only.
3. A tiny-model llama-bench campaign runs end-to-end CPU-only in CI.
4. Docs updated: `docs/CURRENT.md`, `docs/state/status.md`,
   `docs/state/validation-ledger.md` (entries in state `unit-tested`).
5. No performance claim about real hardware is made (that is Block 1.2).

## 6. Explicit non-goals and guardrails

- No knob changes, no privileged actions except the read-only RAPL helper.
- No telemetry upload; no network calls except explicit model fetch commands.
- No lossy defaults; quantized variants appear only as labeled comparison arms.
- No vendor lock-in in core modules: NVIDIA support lives in an optional
  sampler/adapter.

## 7. Order of execution

```
WP1 schemas ──► WP2 probe ──┐
     │                      ├──► WP3 runner/adapters ──► WP6 quality ──┐
     └──► WP4 statistics ───┘           │                               ├──► WP7 store/report ──► WP8 CLI/CI/runbook
                                        └──► WP5 telemetry ─────────────┘
```

WP4 can start in parallel with WP2 (it only needs the result schema).
