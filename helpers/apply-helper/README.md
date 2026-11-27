# ailx-apply-helper

> Status: Phase 1.3 · Licence: MIT · Standard library only

The only privileged writer of the AI Linux system layer. `ailx` (unprivileged)
computes plans and keeps the transaction journal; every write goes through
this helper via `sudo -n` (or is done directly when `ailx` already runs as
root). Request/response: one JSON object on stdin, one JSON line on stdout.

| Property | Enforcement |
|----------|-------------|
| Independent allow-list | Exact paths / tight regexes for targets and values in `ALLOW`; the caller's knob registry is not trusted (a test asserts both stay in sync) |
| Path safety | Absolute, no `..`, symlinks must resolve below `/sys` or `/proc/sys` |
| Value safety | ≤ 32 chars from `[A-Za-z0-9_.+-]`, plus the per-target regex |
| GPU power | Only `min_limit ≤ W ≤ default_limit` (reductions; no raising above the vendor default) |
| No escalation surface | No shell, no CLI arguments; test overrides are ignored under `sudo`/`pkexec` |
| Audit | One stderr line per request (journald when run from systemd/sudo) |

Installed to `/usr/local/libexec/ailx-apply-helper` by `ailx system install`;
the optional sudoers drop-in (`--with-sudoers`) grants members of group `ailx`
exactly this command without a password.
