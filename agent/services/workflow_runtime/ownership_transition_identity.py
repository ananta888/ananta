"""Deterministic digests and opaque identifiers for workflow transition ownership reservations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.services.workflow_runtime.ownership_records import ExecutionOwnership, RetryBudgetSnapshot
from agent.services.workflow_runtime.ownership_values import (
    _ownership_exact_positive_float,
    _ownership_identity,
    _ownership_namespaced_digest,
    _ownership_opaque_id,
    _ownership_positive_integer,
    _ownership_retry_maximum,
    _ownership_sha256,
)

if TYPE_CHECKING:
    from agent.services.workflow_runtime.ownership_transition_reservation import (
        WorkflowTransitionOwnershipReservationIntent,
        WorkflowTransitionOwnershipReservationReceipt,
        WorkflowTransitionOwnershipRetryConsumption,
    )


def workflow_transition_ownership_intent_digest(
    *,
    transition_id: str,
    runtime_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    effect_ordinal: int,
    lease_seconds: float,
    maximum_retries: int,
) -> str:
    values = {
        "transition_id": _ownership_identity(transition_id, "transition_id"),
        "runtime_id": _ownership_identity(runtime_id, "runtime_id"),
        "tenant_id": _ownership_identity(tenant_id, "tenant_id"),
        "workflow_id": _ownership_identity(workflow_id, "workflow_id"),
        "run_id": _ownership_identity(run_id, "run_id"),
        "step_id": _ownership_identity(step_id, "step_id"),
        "effect_ordinal": _ownership_positive_integer(effect_ordinal, "effect_ordinal"),
        "lease_seconds": _ownership_exact_positive_float(lease_seconds, "lease_seconds"),
        "maximum_retries": _ownership_retry_maximum(maximum_retries),
    }
    return _ownership_namespaced_digest(
        values,
        namespace="workflow-transition-ownership-reservation-intent",
    )


def workflow_transition_ownership_owner_id(*, ownership_intent_digest: str) -> str:
    return _ownership_opaque_id(
        "wfto",
        _ownership_sha256(ownership_intent_digest, "ownership_intent_digest"),
    )


def workflow_transition_ownership_operation_fence_id(*, ownership_intent_digest: str, owner_id: str) -> str:
    return _ownership_opaque_id(
        "wftof",
        _ownership_sha256(ownership_intent_digest, "ownership_intent_digest"),
        _ownership_identity(owner_id, "owner_id"),
    )


def workflow_transition_ownership_attempt_id(*, effect_id: str, operation_fence_id: str) -> str:
    return _ownership_opaque_id(
        "wfta",
        _ownership_identity(effect_id, "effect_id"),
        _ownership_identity(operation_fence_id, "operation_fence_id"),
    )


def workflow_transition_ownership_receipt_id(*, transition_id: str, effect_id: str) -> str:
    return _ownership_opaque_id(
        "wftor",
        _ownership_identity(transition_id, "transition_id"),
        _ownership_identity(effect_id, "effect_id"),
    )


def workflow_transition_ownership_record_digest(value: ExecutionOwnership | None) -> str:
    return _ownership_namespaced_digest(
        value.to_dict() if value is not None else {"absent": True},
        namespace="workflow-transition-ownership-record",
    )


def workflow_transition_ownership_receipt_digest(
    value: WorkflowTransitionOwnershipReservationReceipt,
) -> str:
    raw = value.to_dict()
    raw.pop("receipt_digest", None)
    return _ownership_namespaced_digest(
        raw,
        namespace="workflow-transition-ownership-reservation-receipt",
    )


def _ownership_observation_digest(
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
    current: ExecutionOwnership | None,
    current_history: ExecutionOwnership | None,
    acquired_history: ExecutionOwnership | None,
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
    retry_budget: RetryBudgetSnapshot,
    receipt: WorkflowTransitionOwnershipReservationReceipt | None,
    receipt_alias_digests: tuple[str, ...],
) -> str:
    def stable_record(value: ExecutionOwnership | None) -> dict[str, object] | None:
        if value is None:
            return None
        return value.to_dict()

    return _ownership_namespaced_digest(
        {
            "intent": intent.to_dict(),
            "claim_generation": claim_generation,
            "current": stable_record(current),
            "current_history": stable_record(current_history),
            "acquired_history": stable_record(acquired_history),
            "retry_consumption": (retry_consumption.to_dict() if retry_consumption is not None else None),
            "retry_budget": {
                "tenant_id": retry_budget.tenant_id,
                "run_id": retry_budget.run_id,
                "used": retry_budget.used,
                "maximum": retry_budget.maximum,
            },
            "receipt": receipt.to_dict() if receipt is not None else None,
            "receipt_alias_digests": list(receipt_alias_digests),
        },
        namespace="workflow-transition-ownership-reservation-observation",
    )
