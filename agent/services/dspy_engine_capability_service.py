"""Hub-side projection of worker-reported DSPy capabilities."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from agent.services.dspy_optimization_policy import DspyOptimizationPolicy


class DspyEngineCapabilityService:
    def __init__(self, policy: DspyOptimizationPolicy) -> None:
        self._policy = policy
        self._worker: dict[str, Any] | None = None

    def report_worker(self, report: Mapping[str, Any]) -> None:
        allowed = {"state", "installed_version", "compatibility_profile", "reason_code", "network_probe_performed"}
        if set(report) - allowed or report.get("state") not in {"available", "degraded", "unavailable"}:
            raise ValueError("dspy_capability_report_invalid")
        if report.get("network_probe_performed") is not False:
            raise ValueError("dspy_capability_network_probe_denied")
        self._worker = dict(report)

    def projection(self) -> dict[str, Any]:
        if not self._policy.enabled:
            state, reason = "disabled", "dspy_optimization_disabled"
        elif self._worker is None:
            state, reason = "unavailable", "dspy_worker_capability_missing"
        else:
            state, reason = str(self._worker["state"]), str(self._worker["reason_code"])
        return {
            "state": state,
            "reason_code": reason,
            "mode": self._policy.mode,
            "installed_version": self._worker.get("installed_version") if self._worker else None,
            "compatibility_profile": self._worker.get("compatibility_profile") if self._worker else None,
            "optimizer_capabilities": list(self._policy.allowed_optimizers),
            "program_kinds": list(self._policy.allowed_program_kinds),
            "provider_profiles": list(self._policy.allowed_provider_profiles),
            "metric_sets": list(self._policy.allowed_metric_sets),
            "limits": asdict(self._policy.budgets),
            "policy_digest": self._policy.digest,
            "human_intervention_required": False,
        }


__all__ = ["DspyEngineCapabilityService"]
