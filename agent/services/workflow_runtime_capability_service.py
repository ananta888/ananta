"""Versioned Hub projection of workflow runtime capabilities.

The matrix contains data only.  It deliberately imports no Native, LangGraph or
Temporal implementation so API, CLI, TUI and Angular surfaces can consume one
stable Hub projection without acquiring a runtime dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent.services.workflow_runtime_selection_service import (
    RuntimeCandidate,
    RuntimeHealthPort,
    RuntimeHealthSnapshot,
)

RUNTIME_CAPABILITY_MATRIX_SCHEMA = "ananta.workflow_runtime_capability_matrix.v1"
RUNTIME_CAPABILITY_DESCRIPTOR_SCHEMA = "ananta.workflow_runtime_capability.v1"
REQUIRED_RUNTIME_IDS = frozenset({"ananta-native", "langgraph", "temporal"})
_RUNTIME_MODES = frozenset({"live", "durable"})
_HEALTH_STATES = frozenset({"ready", "degraded", "unavailable", "disabled"})


@dataclass(frozen=True)
class RuntimeCapabilityDescriptor:
    runtime_id: str
    runtime_version: str
    contract_version: str
    mode: str
    capabilities: tuple[str, ...]
    restrictions: tuple[str, ...]
    health: RuntimeHealthSnapshot
    data_localities: tuple[str, ...]
    policy_versions: tuple[str, ...]
    max_timeout_seconds: float | None
    max_tokens: int | None
    max_cost_micros: int | None
    priority: int = 100
    schema: str = RUNTIME_CAPABILITY_DESCRIPTOR_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RuntimeCapabilityDescriptor":
        value = dict(raw)
        health_value = dict(value.get("health") or {})
        runtime_id = str(value.get("runtime_id") or "").strip()
        descriptor = cls(
            runtime_id=runtime_id,
            runtime_version=str(value.get("runtime_version") or "").strip(),
            contract_version=str(value.get("contract_version") or "").strip(),
            mode=str(value.get("mode") or "").strip(),
            capabilities=_clean_tuple(value.get("capabilities")),
            restrictions=_clean_tuple(value.get("restrictions")),
            health=RuntimeHealthSnapshot(
                runtime_id=runtime_id,
                status=str(health_value.get("status") or "").strip(),
                reason_code=str(health_value.get("reason_code") or "").strip(),
            ),
            data_localities=_clean_tuple(value.get("data_localities")),
            policy_versions=_clean_tuple(value.get("policy_versions")),
            max_timeout_seconds=_optional_float(value.get("max_timeout_seconds")),
            max_tokens=_optional_int(value.get("max_tokens")),
            max_cost_micros=_optional_int(value.get("max_cost_micros")),
            priority=int(value.get("priority", 100)),
            schema=str(value.get("schema") or RUNTIME_CAPABILITY_DESCRIPTOR_SCHEMA),
        )
        descriptor.assert_valid()
        return descriptor

    def assert_valid(self) -> None:
        if self.schema != RUNTIME_CAPABILITY_DESCRIPTOR_SCHEMA:
            raise ValueError("runtime_capability_descriptor_schema_unsupported")
        if not self.runtime_id or not self.runtime_version or not self.contract_version:
            raise ValueError("runtime_capability_descriptor_identity_required")
        if self.mode not in _RUNTIME_MODES:
            raise ValueError("runtime_capability_mode_invalid")
        if not self.capabilities:
            raise ValueError("runtime_capabilities_required")
        if not self.restrictions:
            raise ValueError("runtime_restrictions_required")
        if not self.data_localities or not self.policy_versions:
            raise ValueError("runtime_capability_governance_metadata_required")
        self.health.assert_valid()
        if self.health.status not in _HEALTH_STATES:
            raise ValueError("runtime_capability_health_invalid")
        if not self.health.reason_code.startswith("runtime_health_"):
            raise ValueError("runtime_health_reason_code_invalid")

    def to_candidate(self) -> RuntimeCandidate:
        return RuntimeCandidate(
            runtime_id=self.runtime_id,
            version=self.runtime_version,
            mode=self.mode,
            capabilities=frozenset(self.capabilities),
            data_localities=frozenset(self.data_localities),
            policy_versions=frozenset(self.policy_versions),
            max_timeout_seconds=self.max_timeout_seconds,
            max_tokens=self.max_tokens,
            max_cost_micros=self.max_cost_micros,
            priority=self.priority,
        )

    def project(
        self,
        *,
        required_capabilities: frozenset[str],
        health: RuntimeHealthSnapshot | None = None,
    ) -> dict[str, Any]:
        observed_health = health or self.health
        missing = tuple(sorted(required_capabilities - set(self.capabilities)))
        if missing:
            state = "incompatible"
            reason_code = "runtime_capabilities_missing"
        elif observed_health.status in {"unavailable", "disabled"}:
            state = "blocked"
            reason_code = observed_health.reason_code
        elif observed_health.status == "degraded":
            state = "degraded"
            reason_code = observed_health.reason_code
        else:
            state = "compatible"
            reason_code = "runtime_capabilities_satisfied"
        return {
            "schema": self.schema,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "contract_version": self.contract_version,
            "mode": self.mode,
            "capabilities": list(self.capabilities),
            "restrictions": list(self.restrictions),
            "health": {
                "status": observed_health.status,
                "reason_code": observed_health.reason_code,
            },
            "declared_health": {
                "status": self.health.status,
                "reason_code": self.health.reason_code,
            },
            "selection": {
                "state": state,
                "reason_code": reason_code,
                "missing_capabilities": list(missing),
            },
        }


class WorkflowRuntimeCapabilityService:
    """Catalog/health ports plus a deterministic surface-safe projection."""

    def __init__(
        self,
        *,
        matrix_version: str,
        descriptors: tuple[RuntimeCapabilityDescriptor, ...],
        health: RuntimeHealthPort | None = None,
    ) -> None:
        if not str(matrix_version).strip():
            raise ValueError("runtime_capability_matrix_version_required")
        by_id = {value.runtime_id: value for value in descriptors}
        if len(by_id) != len(descriptors):
            raise ValueError("runtime_capability_matrix_duplicate_runtime")
        missing = REQUIRED_RUNTIME_IDS - set(by_id)
        if missing:
            raise ValueError(
                "runtime_capability_matrix_required_runtime_missing:"
                + ",".join(sorted(missing))
            )
        self._matrix_version = str(matrix_version).strip()
        self._descriptors = by_id
        self._health = health

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        health: RuntimeHealthPort | None = None,
    ) -> "WorkflowRuntimeCapabilityService":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema") != RUNTIME_CAPABILITY_MATRIX_SCHEMA:
            raise ValueError("runtime_capability_matrix_schema_unsupported")
        descriptors = tuple(
            RuntimeCapabilityDescriptor.from_mapping(value)
            for value in raw.get("runtimes") or ()
        )
        if not descriptors:
            raise ValueError("runtime_capability_matrix_empty")
        return cls(
            matrix_version=str(raw.get("matrix_version") or ""),
            descriptors=descriptors,
            health=health,
        )

    def list_candidates(self) -> tuple[RuntimeCandidate, ...]:
        return tuple(
            self._descriptors[key].to_candidate()
            for key in sorted(self._descriptors)
        )

    def get_health(self, runtime_id: str) -> RuntimeHealthSnapshot:
        descriptor = self._descriptors.get(str(runtime_id))
        if descriptor is None:
            return RuntimeHealthSnapshot(
                str(runtime_id),
                "unavailable",
                "runtime_health_not_registered",
            )
        if self._health is None:
            return descriptor.health
        try:
            observed = self._health.get_health(descriptor.runtime_id)
            observed.assert_valid()
        except (RuntimeError, ValueError):
            return RuntimeHealthSnapshot(
                descriptor.runtime_id,
                "unavailable",
                "runtime_health_observation_invalid",
            )
        if observed.runtime_id != descriptor.runtime_id:
            return RuntimeHealthSnapshot(
                descriptor.runtime_id,
                "unavailable",
                "runtime_health_observation_binding_mismatch",
            )
        return observed

    def hub_projection(
        self,
        *,
        required_capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        required = frozenset(_clean_tuple(required_capabilities))
        return {
            "schema": RUNTIME_CAPABILITY_MATRIX_SCHEMA,
            "matrix_version": self._matrix_version,
            "required_capabilities": sorted(required),
            "runtimes": [
                self._descriptors[key].project(
                    required_capabilities=required,
                    health=self.get_health(key),
                )
                for key in sorted(self._descriptors)
            ],
        }


def default_workflow_runtime_capability_service(
    *,
    health: RuntimeHealthPort | None = None,
) -> WorkflowRuntimeCapabilityService:
    path = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "workflow_runtime"
        / "runtime_capability_matrix.v1.json"
    )
    return WorkflowRuntimeCapabilityService.from_file(path, health=health)


def _clean_tuple(values: Any) -> tuple[str, ...]:
    return tuple(
        sorted({str(value).strip() for value in values or () if str(value).strip()})
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
