# Runtime Foundation (Phase 1.3): the `ailx` System Layer

> Status: Implemented (build-heavy; CPU-integration-tested; GPU paths hw-queued) · Last updated: 2026-11-27 · Related ADRs: 0004, 0005, 0007, 0008, 0010, 0011 (proposed)

## Summary

- `ailx` is the system-layer CLI, separate from the measurement CLI
  `ailx-bench`; both live in the `ailx` Python package and share the probe
  (`ailx.probe`) — hardware detection is not duplicated.
- **Inventory and diagnosis**: `ailx status`, `ailx hardware`,
  `ailx resources` (live CPU/RAM/GPU/VRAM incl. affinity and cgroup limits),
  `ailx doctor` (ok/info/warn/fail/skip with remedies), `ailx knobs`.
- **Profiles** (`baseline`, `inference`, `training`, `energy-efficiency`) are
  declarative TOML documents (`profile.v0`) over an allow-listed knob registry
  (`knob.v0`). **Every setting is experimental and inactive by default**,
  because no knob is hardware-validated yet; `--include-experimental` is an
  explicit opt-in. Forbidden knobs cannot appear in a profile.
- **Transactions**: plan → journal (fsynced) → write → read-back verify →
  commit; automatic rollback on any failure; dead-man confirmation timer;
  drift detection; crash recovery; single active profile with explicit
  `--replace`.
- **Least privilege**: `ailx` runs unprivileged and keeps the journal; the
  only privileged writer is `ailx-apply-helper` (stdlib only, own allow-list,
  invoked via `sudo -n`). As root or under a test root, writes are direct.
- **Engines**: llama.cpp (pinned commit, static CMake build, variants
  cpu/cpu-native/cuda/vulkan) and vLLM (pinned 0.34.0, isolated venv) with
  integrity verification and reproducible build manifests
  (`engine-manifest.v0`).
- **Measurement integration**: fingerprints carry a `platform` section
  (active profile, applied values, engine manifest digests); workloads can
  reference `engine:<engine>/<binary>`; campaign arms can carry a `profile`
  that is applied transactionally before and rolled back after every run.

## Components

| Module (`python/src/ailx/system/`) | Responsibility |
|------------------------------------|----------------|
| `paths.py` | Layout resolution: env → `/etc/ailx/ailx.toml` → system default (`/var/lib/ailx`, `/opt/ailx/engines`) → per-user fallback; `AILX_ROOT` relocates all host paths (tests, chroots) |
| `knobs.py` + `data/knobs.json` | Registry (9 knobs incl. one `forbidden` entry for transparency), target resolution, unprivileged reads, domain validation |
| `profiles.py` + `data/profiles/*.toml` | Profile loading (built-in, `/etc/ailx/profiles`, state dir), activation rule, change plans, service-conflict detection |
| `executor.py` | Privilege boundary: `DirectExecutor`, `HelperExecutor` |
| `transactions.py`, `watchdog.py` | Journal, apply/verify/rollback, confirm, drift, recovery, dead-man watchdog |
| `resources.py` | Dynamic resource snapshot on top of the static hardware profile |
| `doctor.py` | Diagnosis |
| `engines.py` + `data/engines.json` | Engine catalog, install/verify/use/remove/env |
| `install.py` | System-layer install/uninstall with install manifest |
| `integration.py` | Fingerprint `platform` section; profile-per-arm context |
| `../../helpers/apply-helper/` | Privileged helper with independent allow-list |

## Profile activation rule

A setting is applied only if **all** hold:

1. not experimental, and its knob is `hw-validated` — *or* the operator passed
   `--include-experimental` (currently required for every setting);
2. knob `security_class` is `allowed` (or `explicit-opt-in` with
   `--allow-opt-in`); `forbidden` knobs are rejected at load time;
3. no active service manages the knob (TuneD, power-profiles-daemon, tlp, …) —
   AI Linux yields instead of fighting other tuners;
4. the interface exists and the kernel offers the value (e.g. THP choices).

## Transaction semantics

| Situation | Behaviour |
|-----------|-----------|
| Apply | Lock → re-read current values (plans may be stale) → journal fsynced → write → read-back verify per target → `committed` |
| Write or verification fails | Already-applied targets restored in reverse order → `failed-rolled-back` |
| `--confirm-within N` | `pending-confirmation`; detached watchdog rolls back at the deadline unless `ailx confirm` |
| Another profile active | Refused; `--replace` rolls back first, then applies |
| Value changed by someone else (drift) | Reported by `status`/`doctor`; rollback does not overwrite it (`rolled-back-partial`) unless `--force` |
| Crash mid-apply | Journal stays `applying`; new applies are refused; `ailx rollback --recover` restores all *before* values |
| Concurrency | One `flock` on the state directory |

Changes are runtime-only (T1): nothing persists across reboot. Boot
persistence (re-apply at boot) is deliberately not implemented before any
setting is hardware-validated.

## Engines

| Engine | Pinned | Isolation | Integrity | Notes |
|--------|--------|-----------|-----------|-------|
| llama.cpp | commit `98cc730` | `/opt/ailx/engines/llama.cpp/<commit12>-<variant>/` built in a staging dir, atomically renamed; static binaries (`BUILD_SHARED_LIBS=OFF`); sources and build tree removed | sha256 of every binary; smoke test (`llama-bench --version`) recorded | `--source` allows a mirror; recorded in the manifest |
| vLLM | `vllm==0.34.0` | own venv at its final path (venvs are not relocatable), `.incomplete` marker until done | sha256 of every dist-info `RECORD`; `verify --full` re-hashes every installed file against RECORD | CUDA install needs the workstation (hw-queued) |

Manifests record source, flags, targets, toolchain versions (cc, c++, cmake,
git, nvcc), host and artifacts. Binaries are not bit-reproducible across
toolchains; the manifest identifies the build. Its canonical sha256 is the
runtime-bundle digest recorded in measurement fingerprints.

## Installation concept (host distribution: Ubuntu 26.04 LTS)

```bash
sudo python3 -m venv /opt/ailx/venv
sudo /opt/ailx/venv/bin/pip install ./python            # later: a release wheel / .deb
sudo /opt/ailx/venv/bin/ailx system plan --with-sudoers # review what will be installed
sudo /opt/ailx/venv/bin/ailx system install --with-sudoers
sudo usermod -aG ailx "$USER"                           # re-login
ailx doctor
```

`ailx system install` creates the `ailx` group, `/etc/ailx`, `/var/lib/ailx`
(setgid, group-writable), `/opt/ailx/engines`, the helper and RAPL collector
(shebang `/usr/bin/python3 -I`), the RAPL unit (not enabled),
`/etc/ailx/ailx.toml`, optional `/etc/sudoers.d/ailx` (validated with
`visudo -c`) and `/usr/local/bin/{ailx,ailx-bench}` symlinks. It never
overwrites foreign files (unless `--force`, with backup) and records
everything in `/var/lib/ailx/install-manifest.json`. `ailx system uninstall`
removes only unmodified recorded files, refuses while a profile is active, and
`--purge` also removes state, engines and the group. No kernel parameter, boot
entry or security setting is touched. A native `.deb` is the planned
packaging once the layout is stable (open question).

## Measurement integration

```json
{"id": "inference", "description": "…", "profile": {"name": "inference", "include_experimental": true}}
```

A campaign arm with `profile` is applied before each run (warm-up and
measured) and rolled back after it; the fingerprint is taken while the
profile is active, so `fingerprint_differences` in the analysis shows the
`platform` delta between arms. Template:
`workloads/hw/campaign-profile-inference.json`.

## Limits

- GPU paths (NVML resources, NVIDIA power-limit knob, CUDA/Vulkan engine
  builds, vLLM CUDA install) are tested only with a fake `nvidia-smi` or not at
  all — `hw-queued` in `docs/state/validation-ledger.md`.
- No profile setting is validated; profiles are a mechanism, not a
  performance promise.
- AMD/Intel GPU control knobs are not implemented yet (detection only).
