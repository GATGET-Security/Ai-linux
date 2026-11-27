# Community, Governance and Licensing Strategy

> Status: Phase 0 proposal · Last updated: 2026-11-26 · Related ADRs: 0004, 0009 · Evidence: `docs/research/05-base-distributions-and-licensing.md` Part 2 [D-*], `docs/research/01-landscape.md` [L-*] · **Not legal advice; licence items require legal review**

## Summary

- **Licences (proposed, ADR-0009):** `Apache-2.0 OR MIT` for userspace,
  `GPL-2.0-only` for kernel code and BPF programs (sched_ext requires a
  GPL-compatible licence [D-57][D-58]), `CC-BY-4.0` for documentation,
  DCO instead of a CLA, SPDX/REUSE headers everywhere.
- **Vendor neutrality is a design requirement**, not a slogan: Intel's
  single-sponsor Clear Linux ended abruptly in 2025 [L-26]. AI Linux should
  never depend on one company's funding or approval.
- **The community's most valuable contribution is evidence**: hardware
  validation runs, reproductions of claims, counter-evidence and knob
  definitions — not only code.
- **Privacy by design**: result sharing and community profiles are opt-in,
  inspectable and anonymizable; no telemetry (ADR-0004).

## 1. Licensing

| Artifact | Licence | Reason |
|----------|---------|--------|
| Userspace (CLI, daemon, harness, planner, recipes) | `Apache-2.0 OR MIT` | Patent grant via Apache-2.0; GPLv2 compatibility via MIT [D-78][D-79][D-34] |
| Kernel patches/modules | `GPL-2.0-only` | Kernel requirement |
| BPF programs (sched_ext, LSM) | `GPL-2.0-only` (optionally `OR BSD-2-Clause`) | Verifier rejects non-GPL struct_ops [D-58] |
| Docs | `CC-BY-4.0` (snippets `MIT`) | Reusable by distros/vendors |
| Benchmark data | `CC-BY-4.0` (to be confirmed) | Reuse with attribution |

Third-party components: proprietary drivers/toolkits and model weights are
fetched on demand with explicit licence acceptance, recorded in manifests and
SBOMs, never redistributed by default [D-61][D-63][D-64][D-70][D-71].

## 2. Governance roadmap

| Stage | Trigger | Structure |
|-------|---------|-----------|
| 0 — Founding | now | Founding maintainers; public charter (`GOVERNANCE.md`), decision process = ADRs + lazy consensus; Code of Conduct (e.g. Contributor Covenant) |
| 1 — Maintainer group | first external maintainers | Area maintainers (kernel/scheduling, runtimes, harness, image, providers); `CODEOWNERS`; two-maintainer rule for guardrail-relevant code |
| 2 — Steering council | ≈10 regular contributors from ≥3 organizations | Elected council (≈5 seats), term limits, no single organization holds a majority |
| 3 — Foundation | sustained multi-org participation, OpenSSF Best Practices silver | Evaluate LF AI & Data (Sandbox → Incubation) or similar neutral home for trademark, IP and funds [D-87][D-88][D-80] |

Security process: `SECURITY.md`, private reporting, embargo policy, CVE
pathway; OpenSSF Best Practices *passing* in Phase 1, *silver* before a
foundation application [D-80].

## 3. Contribution model

- **DCO sign-off** on every commit, checked in CI [D-84].
- **Evidence rules apply to all contributions** (ADR-0003): performance claims
  need result IDs; knob proposals need security/quality classification and a
  falsifiable expected effect.
- **AI-agent contributions are welcome** and follow `AGENTS.md`; they are
  reviewed by a human maintainer before merge, and agents never flip ADRs to
  "Accepted".
- **Upstream first**: improvements to engines, schedulers and drivers go to the
  respective upstream projects; AI Linux carries patches only temporarily and
  tracks them in a patch ledger.

## 4. Community programs (planned)

| Program | What | Safeguards |
|---------|------|-----------|
| Hardware validation network | Volunteers run signed harness campaigns on their hardware and submit result records | Opt-in; fingerprints can be coarsened; results verified by reproducibility checks before they influence defaults |
| Knob registry | Community-maintained knob definitions with evidence and classification | Guardrail review by maintainers; `forbidden` class cannot be changed by contributors |
| "Reproduce a claim" | Any published claim can be challenged by a reproduction attempt; failed reproductions are published | Claims without reproduction are labeled |
| Profile exchange | Hardware-keyed tuning profiles with provenance | Never auto-applied without local validation (optimizer re-measures) |

## 5. Communication

Public by default: issues and ADR discussions in the repository; a forum or
chat channel is to be chosen (open question). Roadmap and decisions are
documented in `docs/` — not in private channels.

## Open questions

Legal review of the licence set, trademark registration, CRA steward status,
choice of communication platform and Code of Conduct version are tracked in
`docs/state/open-questions.md` ("Licensing and community").
