"""Hub-owned capability projection and preflight policy for HRM experiments."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.services.hrm_experiments.contracts import (
    HrmContractValidator,
    default_hrm_contract_validator,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UNAVAILABLE_DIGEST = hashlib.sha256(b"ananta.hrm.runtime.unavailable.v1").hexdigest()
_REQUIRED_ISOLATION_CONTROLS = (
    "non_root",
    "no_new_privileges",
    "cap_drop_all",
    "read_only_rootfs",
    "network_denied",
    "cgroup_limits",
    "seccomp",
    "mac_policy",
)
_DEFAULT_LIMITS: dict[str, Any] = {
    "cpu_millis": 1000,
    "memory_bytes": 536_870_912,
    "pids": 64,
    "wallclock_seconds": 300,
    "scratch_bytes": 1_073_741_824,
    "output_bytes": 67_108_864,
    "log_bytes": 8_388_608,
    "event_count": 10_000,
    "retries": 0,
    "gpu_device_ids": [],
    "vram_bytes": 0,
}


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class HrmWorkerCapability:
    """Narrow worker capability snapshot consumed by the Hub policy service."""

    worker_id: str
    runtime: Mapping[str, Any]
    device: Mapping[str, Any]
    isolation: Mapping[str, Any]
    supported_profiles: tuple[str, ...]


class HrmWorkerCapabilityPort(Protocol):
    """Read-only port; it cannot dispatch work or mutate Worker state."""

    def read_capability(self) -> HrmWorkerCapability | None:
        """Return an authenticated capability snapshot or no available Worker."""


class UnavailableHrmWorkerCapabilityPort:
    """Default-deny adapter used until a Worker runtime is explicitly wired."""

    def read_capability(self) -> None:
        return None


class HrmExperimentControlPlaneService:
    """Produce Hub-owned capability and preflight decisions without execution."""

    def __init__(
        self,
        *,
        feature_enabled: bool,
        worker_capabilities: HrmWorkerCapabilityPort | None = None,
        contract_validator: HrmContractValidator | None = None,
        effective_limits: Mapping[str, Any] | None = None,
    ) -> None:
        self._feature_enabled = bool(feature_enabled)
        self._worker_capabilities = (
            worker_capabilities or UnavailableHrmWorkerCapabilityPort()
        )
        self._contracts = contract_validator or default_hrm_contract_validator
        self._effective_limits = dict(effective_limits or _DEFAULT_LIMITS)
        self._policy_digest = _canonical_digest(
            {
                "schema": "ananta.hrm-experiments.policy.v1",
                "feature_enabled": self._feature_enabled,
                "required_isolation_controls": list(_REQUIRED_ISOLATION_CONTROLS),
                "effective_limits": self._effective_limits,
            }
        )

    def capability(self) -> dict[str, Any]:
        """Return the closed Hub projection; no runtime authority is granted."""

        capability, _ = self._build_capability()
        return capability

    def preflight(self, *, project_id: str, profile_id: str) -> dict[str, Any]:
        """Evaluate feature, capability and isolation gates without dispatching."""

        self._require_identifier("project_id", project_id)
        self._require_identifier("profile_id", profile_id)
        capability, worker_available = self._build_capability()
        reasons: list[str] = []
        if not self._feature_enabled:
            reasons.append("hrm.feature_disabled")
        elif not worker_available:
            reasons.append("hrm.worker_unavailable")
        elif profile_id not in capability["supported_profiles"]:
            reasons.append("hrm.profile_unsupported")
        elif not all(
            capability["isolation"].get(control) is True
            for control in _REQUIRED_ISOLATION_CONTROLS
        ):
            reasons.append("hrm.isolation_policy_unsatisfied")

        result: dict[str, Any] = {
            "schema": "ananta.hrm-experiments.preflight.v1",
            "allowed": not reasons,
            "reason_codes": reasons,
            "profile_id": profile_id,
            "capability_digest": capability["capability_digest"],
            "policy_digest": self._policy_digest,
            "effective_limits": dict(self._effective_limits),
        }
        self._contracts.validate("preflight_result", result)
        return result

    def _build_capability(self) -> tuple[dict[str, Any], bool]:
        snapshot = (
            self._worker_capabilities.read_capability()
            if self._feature_enabled
            else None
        )
        worker_available = snapshot is not None
        fields = self._snapshot_fields(snapshot)
        unsigned: dict[str, Any] = {
            "schema": "ananta.hrm-experiments.capability.v1",
            "worker_id": fields["worker_id"],
            "feature_enabled": self._feature_enabled,
            "runtime": fields["runtime"],
            "device": fields["device"],
            "isolation": fields["isolation"],
            "supported_profiles": fields["supported_profiles"],
        }
        capability = {**unsigned, "capability_digest": _canonical_digest(unsigned)}
        self._contracts.validate("capability_probe", capability)
        return capability, worker_available

    @staticmethod
    def _snapshot_fields(snapshot: HrmWorkerCapability | None) -> dict[str, Any]:
        if snapshot is None:
            return {
                "worker_id": "unavailable",
                "runtime": {
                    "engine_version": "unavailable",
                    "image_digest": _UNAVAILABLE_DIGEST,
                    "python_version": "unavailable",
                    "torch_version": None,
                    "cuda_version": None,
                    "flash_attention_version": None,
                },
                "device": {"kind": "cpu", "device_ids": [], "vram_bytes": 0},
                "isolation": {
                    "profile_version": "unavailable",
                    **{control: False for control in _REQUIRED_ISOLATION_CONTROLS},
                },
                "supported_profiles": [],
            }
        return {
            "worker_id": snapshot.worker_id,
            "runtime": dict(snapshot.runtime),
            "device": dict(snapshot.device),
            "isolation": dict(snapshot.isolation),
            "supported_profiles": sorted(set(snapshot.supported_profiles)),
        }

    @staticmethod
    def _require_identifier(field_name: str, value: str) -> None:
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise ValueError(f"invalid {field_name}")


def default_hrm_experiment_control_plane_service() -> HrmExperimentControlPlaneService:
    """Compose the default-deny Hub service from central application settings."""

    from agent.config import settings
    from agent.services.hrm_experiments.capability_port import (
        SqlHrmWorkerCapabilityPort,
    )

    return HrmExperimentControlPlaneService(
        feature_enabled=bool(settings.hrm_experiments_enabled),
        worker_capabilities=SqlHrmWorkerCapabilityPort(),
    )


__all__ = [
    "HrmExperimentControlPlaneService",
    "HrmWorkerCapability",
    "HrmWorkerCapabilityPort",
    "UnavailableHrmWorkerCapabilityPort",
    "default_hrm_experiment_control_plane_service",
]
