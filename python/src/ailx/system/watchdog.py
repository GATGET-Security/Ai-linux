# SPDX-License-Identifier: MIT
"""Detached dead-man watchdog: ``python -m ailx.system.watchdog <txn-id>``."""

from __future__ import annotations

import sys
import time

from ailx.system.paths import load_layout
from ailx.system.transactions import Manager, TransactionError


def main() -> int:
    txn_id = sys.argv[1]
    mgr = Manager(load_layout())
    deadline = float(mgr.load(txn_id).get("confirm_deadline") or 0)
    while time.time() < deadline:
        time.sleep(min(1.0, max(0.05, deadline - time.time())))
    for _ in range(30):  # the lock may be held briefly by another ailx command
        try:
            mgr.watchdog_expire(txn_id)
            return 0
        except TransactionError:
            time.sleep(1)
    return 1


if __name__ == "__main__":
    sys.exit(main())
