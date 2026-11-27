# Trust-Boundary Review of the System Layer (ADR-0011), Phase 1.4

> Status: review done, fixes implemented and unit-tested · Last updated: 2026-11-27

## Summary

We reviewed the privileged boundary of `ailx`: the unprivileged planner, the
allow-listed helper, and the journaled transactions (ADR-0011, still
**Proposed**). The review looked at six attack and failure classes.

**Five real defects were found and fixed:**

1. A tampered journal could make root write arbitrary files.
2. Transaction IDs allowed path traversal.
3. Symlink swaps between check and write.
4. Root-owned links could point into user-writable trees.
5. Transactions that survived a reboot had no defined state.

Two further gaps were closed:

- The dead-man watchdog could not roll back without passwordless sudo.
- A GPU running above its default power limit could not be restored.

Tests: `python/tests/test_system_security.py` (8 tests). Nothing here is
hardware-validated. The NVIDIA restore path still runs only against a fake
`nvidia-smi` (VL-1.3-04).

## Boundary

```
user (group ailx) ── ailx (unprivileged: plan, journal, verify) ──sudo -n──▶ ailx-apply-helper (root, stdlib, python3 -I)
                         │                                                        │ independent allow-list
                         └── /var/lib/ailx/transactions (root:ailx 2770) ◀────────┘ writes /sys, /proc/sys, nvidia-smi -pl
```

## Findings and fixes

| # | Class | Finding | Fix | Test |
|---|-------|---------|-----|------|
| F1 | Journal manipulation | Journals live in a group-writable directory. `sudo ailx rollback` (direct executor as root) wrote the `target`/`value` taken from the journal without checking them again. A member of group `ailx` could therefore make root write arbitrary content to any file. | `knobs.validate_write()` runs before **every** write, including reverts. The target must be one that the live registry resolves for that knob *now*. The value must match `[A-Za-z0-9_.+-]{1,32}` and the knob's domain. GPU power must satisfy `min ≤ W ≤ default`. The helper keeps its own, independent allow-list. | `test_tampered_journal_target_is_refused_on_rollback`, `test_tampered_journal_value_is_refused` |
| F2 | User-writable paths | A transaction ID taken from the CLI or the state file was joined into a path unchecked, so `../` could escape the journal directory. | IDs must match `^txn-\d{8}T\d{6}-[0-9a-f]{6}$` in `load()` and in the state file. | `test_invalid_transaction_ids_are_rejected` |
| F3 | TOCTOU | The target was checked first and opened later by name, so a symlink swapped in between was followed. | The executor and the helper resolve the path once (`realpath`), require it to stay under `/sys/` or `/proc/sys/` of the layout root, and then open exactly that resolved path with `O_WRONLY\|O_TRUNC\|O_NOFOLLOW\|O_CLOEXEC`. | `test_direct_executor_refuses_symlink_escape`; existing helper tests |
| F4 | Manipulable imports | (a) In layer mode, `ailx system install` linked `/usr/local/bin/ailx` to the entry point of whatever venv it was run from. If root later ran that link and the venv was user-writable, root would import user-controlled code. (b) Unpackaged scripts honoured `PYTHONPATH` and the user site directory. | (a) Root links are created only when every path component of the target tree is root-owned and not group- or world-writable (`_trusted_tree`). (b) The `.deb` installs code into root-owned `dist-packages`. All entry points and helpers use `#!/usr/bin/python3 -I` (isolated mode: no `PYTHONPATH`, no user site, no script directory). `sudo` resets the environment. | `test_untrusted_trees_get_no_root_symlinks`; shebangs shown by the deb lifecycle test |
| F5 | Reliable rollback | All changes are runtime-only, so a reboot resets them. Without a defined state, a transaction that was still "active" after a reboot would "restore" stale values over the new boot defaults. | Each transaction records `boot_id`. `reconcile()`, called before apply and rollback, marks such a transaction `expired-by-reboot` and clears the active profile. | `test_reboot_expires_runtime_transaction` |
| F6 | Reliable rollback | The dead-man watchdog (`--confirm-within`) runs unattended. If `sudo` asks for a password, the automatic rollback fails silently. | `apply` with a confirm timeout first checks `sudo -n -l <helper>`. If that fails, it refuses with an explanation and changes nothing. | covered by the transaction tests (executor without sudo) |
| F7 | GPU power limit | The helper only ever lowers a limit (`min ≤ W ≤ default`). If a GPU already runs above its vendor default, for example raised by another tool, the original value could not be restored, and the rollback would fail. | The planner skips such a GPU and says why. It manages only GPUs whose current limit is ≤ the default, so the original value is always restorable. | `test_gpu_above_default_is_not_managed`, `test_gpu_restore_returns_original_limit` |

### Bug found by the fixes themselves

The hardened write first used `O_WRONLY` without `O_TRUNC`. A shorter value
written over a longer one left a mixed result, for example
`performanceformance`. The live read-back test caught this, and both writers now
truncate.

## Residual risks (accepted for Phase 1.x, revisit with the Phase 2 daemon)

- **Group `ailx` is a privilege.** Its members can apply allow-listed values
  through the helper (with the opt-in sudoers rule) and can edit journals. After
  F1 that means at most allow-listed writes with in-domain values, which is the
  intended capability of the group. Document it when adding operators.
- **Intermediate path components.** `O_NOFOLLOW` protects only the final
  component. Under `/sys` and `/proc/sys`, the directories are kernel-controlled
  and not writable by unprivileged users, so the remaining window cannot be
  exploited there. `openat2(RESOLVE_NO_SYMLINKS)` would close it in general, but
  the Python stdlib does not offer it.
- **NVIDIA restore depends on the driver.** Restoring uses `nvidia-smi -pl`
  with the journaled original value. Whether a limit set this way survives a
  driver reload or a reboot has not been verified (**PK**: it does not persist).
  This stays `hw-queued` (VL-1.3-04).
- **`sudo ailx` from a user-owned venv** runs that user's own code as root.
  This is the operator's own choice. The packaged layout avoids it, and the
  runtime-foundation documentation recommends the packaged or sudoers path.

## References

- ADR-0011 (Proposed) · `docs/architecture/runtime-foundation.md` ·
  `helpers/apply-helper/README.md`
