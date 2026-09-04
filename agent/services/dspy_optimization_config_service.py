"""Pure config loader producing a digest-bound audit projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.dspy_optimization_policy import DspyOptimizationPolicy


class DspyOptimizationConfigService:
    def load(
        self, raw: Mapping[str, Any], *, enabled: bool, mode: str
    ) -> tuple[DspyOptimizationPolicy, dict[str, Any]]:
        policy = DspyOptimizationPolicy.from_mapping({**dict(raw), "enabled": enabled, "mode": mode})
        return policy, {
            "schema": "ananta.dspy-config-audit.v1",
            "action": "dspy_policy_loaded",
            "policy_digest": policy.digest,
            "enabled": policy.enabled,
            "mode": policy.mode,
            "unsafe_capabilities_enabled": False,
            "secret_fields_present": False,
        }


__all__ = ["DspyOptimizationConfigService"]
