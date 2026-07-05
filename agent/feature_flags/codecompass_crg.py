"""Feature flags for the code-review-graph adapter (CRG).

All defaults are ``off`` *except* safety properties listed in
``SAFETY_ON`` which default to ``on`` and cannot be disabled via
environment. The adapter is optional and never required for native
Ananta graph capabilities.
"""
from __future__ import annotations

GROUP = "crg"

# Safety properties default on; env cannot turn them off.
SAFETY_ON = frozenset({"strict_pinning"})


def flags() -> dict[str, bool]:
    return {
        "adapter_enabled": False,
        "strict_pinning": True,
        "allow_direct_sqlite_read": False,
    }