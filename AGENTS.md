# AGENTS.md — canonical instructions for all agents (human or AI)

This file is the **single source of truth** for how to work in this repository.
Tool-specific files (`CLAUDE.md`, editor rules, etc.) must only point here and
must not contradict it. If they conflict, this file wins.

## 1. Project in one paragraph

**AI Linux** (working name) is an open-source, Linux-based compute platform
optimized from the ground up for *local* AI workloads: LLMs, image/video/audio
generation, embeddings, autonomous agents, training and fine-tuning. The goal is
the maximum achievable performance of existing hardware **at unchanged model
quality and system stability**. It is not "a distro with AI tools
preinstalled"; it is a measured, self-optimizing platform that reuses the best
existing open-source components.

## 2. Current phase

**Phase 0 — Research & Architecture.** No distribution, no production code.
Allowed outputs: research notes, architecture documents, ADRs, plans,
benchmark *designs*. Read `docs/CURRENT.md` for the live state before doing
anything else.

## 3. Non-negotiable principles (see ADR-0004)

1. **AI-first, performance-first, local-first, hardware-agnostic, modular,
   community-driven.**
2. **Never disable a security mechanism** (CPU mitigations, IOMMU isolation,
   Secure Boot/lockdown, LSMs, KASLR, …) to obtain an unproven performance gain.
   Security-reducing knobs are either forbidden or require explicit, informed,
   per-machine user opt-in with documented evidence.
3. **No silent quality reduction.** Any lossy technique (quantization, lossy
   attention, step caching, KV-cache quantization, …) must be visible, labeled
   and opt-in. Automated tuning may only auto-adopt changes that pass the
   quality gate.
4. **No forced telemetry, no cloud dependency.** Everything works offline.
   Any data sharing is opt-in, documented and inspectable.
5. **All own code is open source.** Proprietary drivers/toolkits (e.g. NVIDIA
   userspace, CUDA) may be supported as optional components, never required by
   the core.
6. **Reuse before reinvent.** Integrate high-quality upstream projects; only
   build what does not exist or cannot be composed.

## 4. Evidence rules (see ADR-0003)

- **Never invent benchmark numbers.** A performance number in this repo needs
  either a cited external source (with hardware/model/settings context) or a
  result ID from our own benchmark store (once it exists).
- Every source gets: URL, date (published/updated or `n.d.`), access date, and
  an **evidence grade A–D** (definitions in `docs/research/00-method.md`).
- Conjectures are labeled **Hypothesis H-<area>-NN** and listed in
  `docs/state/hypotheses.md` with a falsification test.
- Record contradictory evidence instead of picking the convenient source.
- Theoretical bounds (e.g. bandwidth math) must show the formula and be labeled
  *theoretical*.

## 5. Context tiers — keep the hot context small

| Tier | Files | Rule |
|------|-------|------|
| Hot (always read) | `AGENTS.md`, `docs/CURRENT.md` | `CURRENT.md` ≤ 100 lines; summarize, link, never duplicate |
| Warm (read per task) | `docs/INDEX.md`, relevant `docs/architecture/*`, `docs/adr/*`, `docs/state/*` | Keep each doc focused on one topic |
| Cold (on demand) | `docs/research/*` (long, source-heavy) | Cite by ID; do not paste into hot files |

At the end of a work session, update `docs/CURRENT.md` (state, next step) and,
if anything changed, `docs/state/status.md`. Add new documents to
`docs/INDEX.md`.

## 6. Repository map

```
README.md            public overview
AGENTS.md            this file (canonical agent instructions)
CLAUDE.md            pointer to AGENTS.md
docs/CURRENT.md      hot context: phase, decisions, next block
docs/INDEX.md        map of all documents
docs/research/       evidence-graded research notes (cold)
docs/architecture/   target architecture, methodology, test plans
docs/state/          status, roadmap, risks, open questions, hypotheses
docs/adr/            architecture decision records (NNNN-title.md)
```

## 7. Decisions (ADRs)

- Any decision that is hard to reverse or affects multiple components needs an
  ADR in `docs/adr/` using `0000-template.md`.
- Status values: `Proposed` → `Accepted` / `Rejected` → `Superseded by ADR-XXXX`.
- Do not silently change an accepted ADR; supersede it.
- "Proposed" ADRs need maintainer approval; agents must not flip them to
  "Accepted" on their own.

## 8. Writing conventions

- Repository documentation is **English**, GitHub-flavored Markdown.
- Prefer tables for comparisons; keep summaries at the top of every doc.
- Each doc starts with a status line:
  `> Status: … · Last updated: YYYY-MM-DD`.
- Cite with bracketed IDs (`[K-03]`) resolving to the doc's `Sources` table.
- Mark every technique as **lossless** or **lossy** where relevant.

## 9. Development strategy for later phases: build-heavy / deferred validation (ADR-0010)

When implementation starts:
- Agents and CI may write, build and unit-test code without access to the
  target hardware. Heavy builds (kernels, images, runtime bundles) run in CI /
  build farms.
- Anything whose value depends on real hardware is **not considered validated**
  until it runs on the hardware in the test plan. Track it in
  `docs/state/validation-ledger.md` with status
  `authored → built → unit-tested → hw-queued → hw-validated | hw-failed`.
- Performance claims in PRs, release notes or docs must link a result ID from
  hardware validation; otherwise they are labeled hypotheses.
- Never merge a tuning default that has not been hardware-validated on at least
  one target and passed the quality and stability gates.

## 10. Git workflow

- Work on feature branches; small, reviewable commits with descriptive
  messages (`docs: …`, `adr: …`, `research: …`).
- Do not commit secrets, credentials, model weights, or large binaries.
- Do not rewrite shared history.

## 11. Things agents must not do

- Fabricate measurements, sources, quotes, version numbers or dates.
- Add telemetry, auto-updaters that phone home, or cloud requirements.
- Add knobs that reduce security or quality as defaults.
- Expand the hot context with research detail.
- Start building the distribution while Phase 0 is active.
