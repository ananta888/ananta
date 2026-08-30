"""Bounded Worker capability projection for research training."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.research_training_policy import ResearchTrainingPolicy
from ananta_contracts.research_training import STAGE_CAPABILITIES, require_id


class ResearchTrainingCapabilityService:
    def __init__(self, policy: ResearchTrainingPolicy) -> None:
        self._policy = policy
        self._worker: dict[str, Any] | None = None

    def report_worker(self, report: Mapping[str, Any]) -> None:
        expected = {
            "state",
            "reason_code",
            "engine_version",
            "capabilities",
            "gpu_profiles",
            "network_probe_performed",
        }
        if set(report) != expected:
            raise ValueError("research_worker_capability_fields_invalid")
        state = str(report.get("state") or "").strip().lower()
        if state not in {"available", "degraded", "unavailable"}:
            raise ValueError("research_worker_capability_state_invalid")
        capabilities = report.get("capabilities")
        profiles = report.get("gpu_profiles")
        if not isinstance(capabilities, list) or not isinstance(profiles, list):
            raise ValueError("research_worker_capability_list_invalid")
        if len(capabilities) > 32 or len(profiles) > 32:
            raise ValueError("research_worker_capability_list_invalid")
        normalized_capabilities = sorted({require_id(item, "capability") for item in capabilities})
        if any(item not in set(STAGE_CAPABILITIES.values()) for item in normalized_capabilities):
            raise ValueError("research_worker_capability_unknown")
        if not isinstance(report.get("network_probe_performed"), bool):
            raise ValueError("research_worker_network_probe_invalid")
        self._worker = {
            "state": state,
            "reason_code": str(report.get("reason_code") or "")[:128] or None,
            "engine_version": str(report.get("engine_version") or "")[:64] or None,
            "capabilities": normalized_capabilities,
            "gpu_profiles": sorted({require_id(item, "gpu_profile") for item in profiles}),
            "network_probe_performed": bool(report["network_probe_performed"]),
        }

    def supports(self, capabilities: set[str]) -> bool:
        if self._worker is None or self._worker["state"] != "available":
            return False
        return capabilities <= set(self._worker["capabilities"])

    def projection(self) -> dict[str, Any]:
        worker = self._worker or {
            "state": "unavailable",
            "reason_code": "research_worker_not_reported",
            "engine_version": None,
            "capabilities": [],
            "gpu_profiles": [],
            "network_probe_performed": False,
        }
        available = self._policy.enabled and worker["state"] == "available"
        return {
            "schema": "ananta.research-training-capability.v1",
            "state": "available" if available else ("disabled" if not self._policy.enabled else worker["state"]),
            "available": available,
            "reason_code": None if available else (
                "research_training_disabled" if not self._policy.enabled else worker["reason_code"]
            ),
            "mode": self._policy.mode,
            "automatic_release_enabled": self._policy.automatic_release_enabled,
            "worker": dict(worker),
            "experimental": True,
            "not_production_ready": True,
            "claims_not_verified": True,
            "human_intervention_required": False,
        }


__all__ = ["ResearchTrainingCapabilityService"]
