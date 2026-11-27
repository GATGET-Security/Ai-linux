# ailx-bench

> Status: Phase 1.1 · Licence: MIT · Python ≥ 3.12

Measurement foundation of AI Linux: hardware probe, environment fingerprint,
pre-registered A/B campaigns with randomized interleaving, nonparametric
statistics, telemetry, KLD quality gate, content-addressed result store and
reports whose numbers are traceable to stored IDs.

```bash
pip install -e ".[dev]"            # extras: nvidia (NVML), gguf (vocabulary checks)
ailx-bench probe                   # hardware profile + fingerprint (lists unavailable fields)
ailx-bench campaign all ../workloads/campaigns/synthetic-aa.json --report aa.md
ailx-bench campaign all ../workloads/campaigns/synthetic-effect-5pct.json --calibration <ana-id> --report eff.md
ailx-bench store verify
```

Synthetic campaigns in `simulated` mode validate the harness; their results are
labeled `measurement_kind: simulated` and reports carry a banner. For real
hardware see `docs/runbooks/first-hardware-run.md`.

Development: `ruff check src tests ../tools ../helpers && ruff format --check src tests ../tools ../helpers && mypy && pytest`.
Tests marked `llamacpp` need `AILX_LLAMA_CPP_BIN`, `AILX_TINY_DIR`, `AILX_LLAMA_BENCH`, `AILX_TINY_MODEL` (see CI).
