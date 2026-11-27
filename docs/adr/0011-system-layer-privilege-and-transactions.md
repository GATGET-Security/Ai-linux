# ADR-0011: System layer — unprivileged planner, allow-listed privileged helper, journaled transactions

> Status: Accepted (maintainer decision 2026-11-27, incl. the Phase 1.4 review amendment) · Date: 2026-11-27 · Deciders: maintainers

## Context

Phase 1.3 introduces the first code that changes host state (kernel runtime
knobs, cpufreq, GPU power limit). ADR-0004 forbids weakening security and
silent quality changes; ADR-0008 requires tiered, reversible adoption with
dead-man protection; ADR-0010 says nothing is validated before hardware runs.
TuneD's local privilege-escalation CVEs show that a root tuning daemon is attack
surface [S-01].

## Options considered

1. **Run all of `ailx` as root** (sudo per command) — simple; the whole Python
   dependency tree becomes privileged attack surface.
2. **Privileged daemon with IPC** (polkit/D-Bus or varlink) — strong model,
   but heavy for Phase 1 and long-running privileged code.
3. **Unprivileged planner + single-request privileged helper** with its own
   allow-list, invoked via `sudo -n` (or direct writes when already root).

## Decision

Option 3 for Phase 1.x:

- `ailx` computes plans and keeps the journal unprivileged; the only
  privileged writer is `ailx-apply-helper` (stdlib only, isolated-mode Python,
  independent allow-list of targets and values, GPU power limit only between
  min and default, no CLI arguments, test overrides ignored under sudo/pkexec).
- All changes go through journaled transactions (journal fsynced before
  writes, read-back verification, automatic rollback, dead-man confirmation,
  drift detection, crash recovery, one active profile).
- Profiles are declarative; settings not hardware-validated are experimental
  and inactive by default; forbidden knobs cannot be expressed.
- Changes are runtime-only; no boot persistence until settings are
  hardware-validated.
- Revisit option 2 (daemon) when Phase 2 needs continuous tuning.

## Consequences

- Small privileged surface that can be reviewed in isolation; consistency
  between registry and helper allow-list is enforced by a test.
- Operators need either sudo per command or the opt-in sudoers drop-in
  (`ailx system install --with-sudoers`).
- Runtime-only changes are lost on reboot — intentional until validated.

## Review amendment (Phase 1.4)

A trust-boundary review (`docs/architecture/trust-boundary-review.md`) found
and fixed seven issues; the decision itself is unchanged:

- Journals are untrusted input. Every write, including reverts, is re-validated
  against the live registry (F1), and transaction IDs are validated (F2).
- Writes open the resolved path with `O_NOFOLLOW` (F3). Root-owned links point
  only into trusted trees, and packaged entry points run `python3 -I` (F4).
- Transactions carry the boot ID and expire on reboot (F5). The dead-man
  watchdog requires passwordless sudo up front (F6).
- GPUs above their default power limit are not managed, so the original
  value is always restorable (F7).

Delivery changed: the `.deb` is now the primary install path.
`ailx system install` remains for unpackaged checkouts only.

## References

- `docs/architecture/runtime-foundation.md`
- `helpers/apply-helper/README.md`
- `docs/architecture/trust-boundary-review.md`
