"""Feature flags for the Repository Intelligence Graph (RIG) layer.

Defaults are ``off``. Manual JSON fixtures (RIG-012) are *not* gated by
``rig.adapter_enabled`` so that escape-valve usage does not require a
provider switch.

Safety property: ``strict_coverage_gating`` is listed in ``SAFETY_ON``
and cannot be disabled via environment — flipping it off would allow
non-authoritative RIG data to be treated as authoritative.
"""
from __future__ import annotations

GROUP = "rig"

SAFETY_ON = frozenset({"strict_coverage_gating"})


def flags() -> dict[str, bool]:
    return {
        "adapter_enabled": False,
        "allow_manual_fixtures": True,
        "strict_coverage_gating": True,
    }