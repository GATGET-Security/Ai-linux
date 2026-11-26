# SPDX-License-Identifier: MIT
"""Read-only hardware discovery and environment fingerprinting (WP2)."""

from ailx.probe.fingerprint import collect_fingerprint
from ailx.probe.hardware import collect_hardware_profile

__all__ = ["collect_fingerprint", "collect_hardware_profile"]
