# Canonical schemas (v0)

> Status: Phase 1.1 · Licence: MIT · JSON Schema draft 2020-12

These schemas are the **language-neutral contract** of the measurement
foundation. Any implementation (Python today; samplers or daemons in other
languages later) must accept every document in `fixtures/valid/` and reject
every document in `fixtures/invalid/`.

| Schema | `schema` value | ID prefix | Purpose |
|--------|---------------|-----------|---------|
| `hardware-profile.v0` | `ailx.hardware-profile/v0` | `hwp-` | Static hardware description incl. LLC domains and accelerator memory domains |
| `environment-fingerprint.v0` | `ailx.environment-fingerprint/v0` | `env-` | Software/config state per run (kernel, security, scheduler, cpufreq, THP, daemons, drivers, models) |
| `workload.v0` | `ailx.workload/v0` | — | Workload manifest (adapter, params, metrics, output-length policy) |
| `campaign.v0` | `ailx.campaign/v0` | `cmp-` | Pre-registered analysis plan with frozen RMIT schedule |
| `result.v0` | `ailx.result/v0` | `res-` | One run: metrics, telemetry summary, energy, stability events, `measurement_kind` |
| `analysis.v0` | `ailx.analysis/v0` | `ana-` | Statistical analysis of a campaign (verdicts, calibration) |
| `sample-stream.v0` | `ailx.sample-stream/v0` | — | One JSON line of the sampler protocol (header / sample / event) |
| `knob.v0` | `ailx.knob/v0` | — | Draft knob metadata (used from Phase 2) |

**IDs**: `<prefix>-sha256(RFC 8785 canonical JSON of the document without "id")`.
The hardware-profile ID additionally excludes `collected_at`, so the same
machine yields the same ID.

**Unavailable data** is never guessed: documents carry an `unavailable` map
(field → reason).

Regenerate the corpus: `python python/tests/make_schema_fixtures.py`.
