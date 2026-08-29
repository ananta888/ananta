"""DSP/ML-runtime-free capability read model owned by the Hub."""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.dendritic_memory_policy import DendriticMemoryPolicy


class DendriticMemoryCapabilityService:
    def __init__(self, policy: DendriticMemoryPolicy) -> None:
        self._policy = policy
        self._worker: dict[str, Any] | None = None

    def report_worker(self, report: Mapping[str, Any]) -> None:
        allowed = {
            "state",
            "reason_code",
            "torch_version",
            "safetensors_version",
            "gpu_profiles",
            "base_models",
            "architecture_versions",
            "network_probe_performed",
        }
        if set(report) - allowed or report.get("state") not in {"available", "degraded", "unavailable"}:
            raise ValueError("dendritic_worker_capability_invalid")
        if report.get("network_probe_performed") is not False:
            raise ValueError("dendritic_worker_capability_network_probe_forbidden")
        for key in ("torch_version", "safetensors_version", "reason_code"):
            value = report.get(key)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128):
                raise ValueError("dendritic_worker_capability_invalid")
        for key in ("gpu_profiles", "base_models", "architecture_versions"):
            values = report.get(key, [])
            if (
                not isinstance(values, (list, tuple))
                or len(values) > 64
                or any(not isinstance(value, str) or not 1 <= len(value) <= 192 for value in values)
            ):
                raise ValueError("dendritic_worker_capability_invalid")
        self._worker = dict(report)

    def projection(self) -> dict[str, Any]:
        if not self._policy.enabled:
            state, reason = "disabled", "dendritic_experiment_disabled"
        elif self._worker is None:
            state, reason = "unavailable", "dendritic_worker_not_reported"
        else:
            state, reason = str(self._worker["state"]), self._worker.get("reason_code")
        return {
            "schema": "ananta.dendritic-memory-capability.v1",
            "state": state,
            "available": state == "available",
            "reason_code": reason,
            "contract_version": "ananta.dendritic-memory-worker.v1",
            "experimental": True,
            "not_production_ready": True,
            "claims_not_verified": True,
            "mode": self._policy.mode,
            "runtime_enabled": self._policy.runtime_enabled,
            "automatic_activation_enabled": self._policy.automatic_activation_enabled,
            "job_types": ["train_dendritic_memory", "evaluate_dendritic_memory", "compose_dendritic_memory"],
            "limits": {
                "max_pack_bytes": self._policy.max_pack_bytes,
                "max_active_packs": self._policy.max_active_packs,
                "max_branches": 64,
                "max_hidden_dimension": 4096,
                "max_steps": 100_000,
            },
            "worker": dict(self._worker) if self._worker else None,
            "health_probe_model_call_performed": False,
            "human_intervention_required": False,
        }


__all__ = ["DendriticMemoryCapabilityService"]
