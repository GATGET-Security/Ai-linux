# Validation Ledger (deferred validation)

> Status: empty template · Last updated: 2026-11-26 · Policy: ADR-0010 (proposed)

Tracks every change or claim whose value depends on real hardware.
States: `authored → built → unit-tested → hw-queued → hw-validated | hw-failed`.

Rules:

- A tuning default, profile or performance claim may only be published when
  its row is `hw-validated` for the shipped component versions.
- A row becomes stale (→ `hw-queued`) when a dependency in its fingerprint
  changes (kernel, driver, runtime bundle, firmware).
- `hw-failed` rows stay in the ledger with the result IDs (negative results are
  knowledge, too).

| ID | Change / claim | Linked hypothesis | Target hardware | State | Result IDs | Fingerprint | Notes |
|----|----------------|-------------------|-----------------|-------|------------|-------------|-------|
| — | *(no entries — Phase 0 produced no code and no measurements)* | — | — | — | — | — | — |
