"""Default N-1 compatibility policy for versioned workflow contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent.services.workflow_runtime._serialization import redact_json, sha256_json
from agent.services.workflow_runtime.errors import UnsupportedSchemaVersion
from agent.services.workflow_runtime.schema_evolution import QuarantinedContract, UpcasterRegistry

RUNTIME_CONTRACT_TARGETS = {
    "plan": "ananta.execution_plan.v1",
    "state": "ananta.workflow_state.v1",
    "checkpoint": "ananta.workflow_checkpoint.v1",
    "event": "ananta.workflow_event.v1",
    "authorization": "ananta.runtime_authorization.v1",
}


def build_default_runtime_upcaster_registry() -> UpcasterRegistry:
    registry = UpcasterRegistry()
    registry.register(
        contract_type="plan",
        source_schema="ananta.execution_plan.v0",
        target_schema=RUNTIME_CONTRACT_TARGETS["plan"],
        upcaster=_upcast_plan_v0,
    )
    registry.register(
        contract_type="state",
        source_schema="ananta.workflow_state.v0",
        target_schema=RUNTIME_CONTRACT_TARGETS["state"],
        upcaster=_upcast_state_v0,
    )
    registry.register(
        contract_type="checkpoint",
        source_schema="ananta.workflow_checkpoint.v0",
        target_schema=RUNTIME_CONTRACT_TARGETS["checkpoint"],
        upcaster=_upcast_checkpoint_v0,
    )
    registry.register(
        contract_type="event",
        source_schema="ananta.workflow_event.v0",
        target_schema=RUNTIME_CONTRACT_TARGETS["event"],
        upcaster=_upcast_event_v0,
    )
    registry.register(
        contract_type="authorization",
        source_schema="ananta.runtime_authorization.v0",
        target_schema=RUNTIME_CONTRACT_TARGETS["authorization"],
        upcaster=_upcast_authorization_v0,
    )
    return registry


class RuntimeContractMigrationService:
    """Upcasts and then validates before migrated data can cross execution boundaries."""

    def __init__(self, registry: UpcasterRegistry | None = None) -> None:
        self._registry = registry or build_default_runtime_upcaster_registry()

    def migrate(
        self,
        payload: Mapping[str, Any],
        *,
        contract_type: str,
        validator: Callable[[dict[str, Any]], Any],
    ) -> dict[str, Any] | QuarantinedContract:
        target = RUNTIME_CONTRACT_TARGETS.get(str(contract_type))
        raw = dict(payload)
        if target is None:
            return _quarantine(raw, contract_type=contract_type, target="unknown", reason="contract_type_unknown")
        migrated = self._registry.upcast_or_quarantine(
            raw,
            contract_type=contract_type,
            target_schema=target,
        )
        if isinstance(migrated, QuarantinedContract):
            return migrated
        try:
            validator(dict(migrated))
        except Exception as exc:  # noqa: BLE001 - validator is an injected contract boundary
            return _quarantine(
                migrated,
                contract_type=contract_type,
                target=target,
                reason=f"target_validation_failed:{type(exc).__name__}",
            )
        return migrated


def upcast_runtime_contract_for_loading(
    payload: Mapping[str, Any],
    *,
    contract_type: str,
) -> dict[str, Any]:
    """Upcast an explicit old schema at a concrete loading boundary.

    Current or omitted schemas retain the established constructor behavior.
    Explicit unknown schemas are quarantined and can never be partially parsed.
    """

    raw = dict(payload)
    target = RUNTIME_CONTRACT_TARGETS.get(str(contract_type))
    if target is None:
        raise UnsupportedSchemaVersion("runtime_contract_type_unknown")
    source = str(raw.get("schema") or "")
    if not source or source == target:
        return raw
    migrated = build_default_runtime_upcaster_registry().upcast_or_quarantine(
        raw,
        contract_type=contract_type,
        target_schema=target,
    )
    if isinstance(migrated, QuarantinedContract):
        raise UnsupportedSchemaVersion(
            "runtime_contract_quarantined:"
            f"{migrated.contract_type}:{migrated.source_schema}:"
            f"{migrated.payload_hash}:{migrated.reason}"
        )
    return migrated


def _upcast_plan_v0(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = {
        **raw,
        "schema": RUNTIME_CONTRACT_TARGETS["plan"],
        "capabilities": list(raw.get("capabilities") or []),
        "gates": list(raw.get("gates") or []),
        "artifacts": list(raw.get("artifacts") or raw.get("artifact_contracts") or []),
    }
    # The canonical hash includes the schema; a v0 hash cannot authenticate v1.
    # Callers recompute it after validating the migrated plan.
    migrated.pop("plan_hash", None)
    migrated.pop("artifact_contracts", None)
    return migrated


def _upcast_state_v0(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        **raw,
        "schema": RUNTIME_CONTRACT_TARGETS["state"],
        "business_data": dict(raw.get("business_data") or {}),
        "runtime_metadata": dict(raw.get("runtime_metadata") or {}),
        "secret_refs": list(raw.get("secret_refs") or []),
        "artifact_refs": list(raw.get("artifact_refs") or []),
        "open_gates": list(raw.get("open_gates") or []),
    }


def _upcast_checkpoint_v0(raw: dict[str, Any]) -> dict[str, Any]:
    state = dict(raw.get("state") or {})
    if state.get("schema") == "ananta.workflow_state.v0":
        state = _upcast_state_v0(state)
    return {
        **raw,
        "schema": RUNTIME_CONTRACT_TARGETS["checkpoint"],
        "state": state,
    }


def _upcast_event_v0(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        **raw,
        "schema": RUNTIME_CONTRACT_TARGETS["event"],
        "step_id": str(raw.get("step_id") or ""),
        "attempt": int(raw.get("attempt") or 0),
        "actor": str(raw.get("actor") or "system"),
        "dedupe_key": str(raw.get("dedupe_key") or raw.get("event_id") or ""),
        "payload": dict(raw.get("payload") or {}),
    }


def _upcast_authorization_v0(raw: dict[str, Any]) -> dict[str, Any]:
    # Rights are narrowed to explicit prior values. Missing tools, artifacts or
    # budgets become empty; an upcaster never grants authority.
    return {
        **raw,
        "schema": RUNTIME_CONTRACT_TARGETS["authorization"],
        "allowed_tools": list(raw.get("allowed_tools") or []),
        "allowed_artifacts": list(raw.get("allowed_artifacts") or []),
        "budgets": dict(raw.get("budgets") or {}),
    }


def _quarantine(
    raw: Mapping[str, Any],
    *,
    contract_type: str,
    target: str,
    reason: str,
) -> QuarantinedContract:
    payload = dict(raw)
    return QuarantinedContract(
        contract_type=str(contract_type),
        source_schema=str(payload.get("schema") or "unknown"),
        target_schema=str(target),
        reason=str(reason),
        payload_hash=sha256_json(payload),
        payload=dict(redact_json(payload)),
    )


__all__ = [
    "RUNTIME_CONTRACT_TARGETS",
    "RuntimeContractMigrationService",
    "build_default_runtime_upcaster_registry",
    "upcast_runtime_contract_for_loading",
]
