"""Row <-> domain mapping of the SQLAlchemy ownership store (exact revalidation of stored rows)."""

from __future__ import annotations

import math

from agent.db_models.workflow_runtime import (
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowTransitionOwnershipReservationDB,
)
from agent.repositories.sqlalchemy_support import (
    stable_row_id,
)
from agent.services.workflow_runtime.ownership import (
    ExecutionOwnership,
    RetryBudgetSnapshot,
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipRetryConsumption,
)


def _ownership_row(value: ExecutionOwnership) -> WorkflowExecutionOwnershipDB:
    return WorkflowExecutionOwnershipDB(
        id=stable_row_id("wfro", value.tenant_id, value.run_id, value.step_id),
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        step_id=value.step_id,
        attempt_id=value.attempt_id,
        owner_id=value.owner_id,
        status=value.status,
        revision=value.revision,
        fencing_token=value.fencing_token,
        lease_expires_at=value.lease_expires_at,
        last_heartbeat_at=value.last_heartbeat_at,
        ownership=value.to_dict(),
    )


def _history_row(value: ExecutionOwnership) -> WorkflowExecutionAttemptHistoryDB:
    return WorkflowExecutionAttemptHistoryDB(
        id=stable_row_id("wfrh", value.tenant_id, value.run_id, value.step_id, value.revision),
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        step_id=value.step_id,
        attempt_id=value.attempt_id,
        owner_id=value.owner_id,
        status=value.status,
        revision=value.revision,
        fencing_token=value.fencing_token,
        recorded_at=value.last_heartbeat_at,
        ownership=value.to_dict(),
    )


def _ownership(row: WorkflowExecutionOwnershipDB) -> ExecutionOwnership:
    return ExecutionOwnership.from_mapping(dict(row.ownership))


def _ownership_exact(row: WorkflowExecutionOwnershipDB) -> ExecutionOwnership:
    try:
        value = ExecutionOwnership.from_exact_mapping(dict(row.ownership))
        if (
            row.id != stable_row_id("wfro", value.tenant_id, value.run_id, value.step_id)
            or row.tenant_id != value.tenant_id
            or row.workflow_id != value.workflow_id
            or row.run_id != value.run_id
            or row.step_id != value.step_id
            or row.attempt_id != value.attempt_id
            or row.owner_id != value.owner_id
            or row.status != value.status
            or row.revision != value.revision
            or row.fencing_token != value.fencing_token
            or row.lease_expires_at != value.lease_expires_at
            or row.last_heartbeat_at != value.last_heartbeat_at
        ):
            raise ValueError("projection")
        return value
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_current_projection_conflict"
        ) from exc


def execution_ownership_from_row(row: WorkflowExecutionOwnershipDB) -> ExecutionOwnership:
    """Validate both projections for a caller that already owns its transaction."""
    return _ownership_exact(row)


def _history_exact(row: WorkflowExecutionAttemptHistoryDB) -> ExecutionOwnership:
    try:
        value = ExecutionOwnership.from_exact_mapping(dict(row.ownership))
        if (
            row.id != stable_row_id("wfrh", value.tenant_id, value.run_id, value.step_id, value.revision)
            or row.tenant_id != value.tenant_id
            or row.workflow_id != value.workflow_id
            or row.run_id != value.run_id
            or row.step_id != value.step_id
            or row.attempt_id != value.attempt_id
            or row.owner_id != value.owner_id
            or row.status != value.status
            or row.revision != value.revision
            or row.fencing_token != value.fencing_token
            or row.recorded_at != value.last_heartbeat_at
        ):
            raise ValueError("projection")
        return value
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_history_projection_conflict"
        ) from exc


def _retry_consumption_exact(
    row: WorkflowRetryConsumptionDB,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
) -> WorkflowTransitionOwnershipRetryConsumption:
    if (
        row.id != stable_row_id("wfrr", intent.tenant_id, intent.run_id, intent.retry_id)
        or row.tenant_id != intent.tenant_id
        or row.run_id != intent.run_id
        or row.retry_id != intent.retry_id
        or row.category != "hub_task"
        or isinstance(row.consumed_at, bool)
        or not isinstance(row.consumed_at, (int, float))
        or not math.isfinite(float(row.consumed_at))
        or float(row.consumed_at) <= 0
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_projection_conflict")
    return WorkflowTransitionOwnershipRetryConsumption(
        tenant_id=row.tenant_id,
        run_id=row.run_id,
        retry_id=row.retry_id,
        category=row.category,
    )


def _retry_budget_exact(
    row: WorkflowRetryBudgetDB | None,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
) -> RetryBudgetSnapshot:
    if row is None:
        return RetryBudgetSnapshot(
            intent.tenant_id,
            intent.run_id,
            used=0,
            maximum=intent.maximum_retries,
        )
    if (
        row.id != stable_row_id("wfrb", intent.tenant_id, intent.run_id)
        or row.tenant_id != intent.tenant_id
        or row.run_id != intent.run_id
        or isinstance(row.used, bool)
        or not isinstance(row.used, int)
        or row.used < 0
        or row.used > 2_147_483_647
        or isinstance(row.maximum, bool)
        or not isinstance(row.maximum, int)
        or row.maximum != intent.maximum_retries
        or row.maximum > 2_147_483_647
        or row.used > row.maximum
        or isinstance(row.revision, bool)
        or not isinstance(row.revision, int)
        or row.revision < 1
        or row.revision != row.used
        or isinstance(row.updated_at, bool)
        or not isinstance(row.updated_at, (int, float))
        or not math.isfinite(float(row.updated_at))
        or float(row.updated_at) <= 0
    ):
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_retry_budget_projection_conflict"
        )
    return RetryBudgetSnapshot(
        intent.tenant_id,
        intent.run_id,
        used=row.used,
        maximum=row.maximum,
    )


def _transition_receipt_row(
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> WorkflowTransitionOwnershipReservationDB:
    intent = receipt.intent
    return WorkflowTransitionOwnershipReservationDB(
        receipt_id=receipt.receipt_id,
        transition_id=receipt.transition_id,
        effect_id=receipt.effect_id,
        operation_fence_id=receipt.operation_fence_id,
        attempt_id=receipt.attempt_id,
        owner_id=receipt.owner_id,
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        runtime_id=intent.runtime_id,
        step_id=intent.step_id,
        ownership_intent_digest=intent.ownership_intent_digest,
        acquisition_record_digest=receipt.acquired_record_digest,
        receipt_digest=receipt.receipt_digest,
        creator_claim_generation=receipt.creator_claim_generation,
        acquired_revision=receipt.acquired_revision,
        acquired_fencing_token=receipt.acquired_fencing_token,
        maximum_retries=intent.maximum_retries,
        retry_consumed=receipt.retry_consumed,
        planned_at=intent.planned_at,
        reserved_at=receipt.reserved_at,
        lease_expires_at=receipt.lease_expires_at,
        receipt=receipt.to_dict(),
    )


def _transition_receipt(
    row: WorkflowTransitionOwnershipReservationDB,
) -> WorkflowTransitionOwnershipReservationReceipt:
    try:
        receipt = WorkflowTransitionOwnershipReservationReceipt.from_mapping(dict(row.receipt))
        intent = receipt.intent
        if (
            row.receipt_id != receipt.receipt_id
            or row.transition_id != receipt.transition_id
            or row.effect_id != receipt.effect_id
            or row.operation_fence_id != receipt.operation_fence_id
            or row.attempt_id != receipt.attempt_id
            or row.owner_id != receipt.owner_id
            or row.tenant_id != intent.tenant_id
            or row.workflow_id != intent.workflow_id
            or row.run_id != intent.run_id
            or row.runtime_id != intent.runtime_id
            or row.step_id != intent.step_id
            or row.ownership_intent_digest != intent.ownership_intent_digest
            or row.acquisition_record_digest != receipt.acquired_record_digest
            or row.receipt_digest != receipt.receipt_digest
            or row.creator_claim_generation != receipt.creator_claim_generation
            or row.acquired_revision != receipt.acquired_revision
            or row.acquired_fencing_token != receipt.acquired_fencing_token
            or row.maximum_retries != intent.maximum_retries
            or type(row.retry_consumed) is not bool
            or row.retry_consumed != receipt.retry_consumed
            or row.planned_at != intent.planned_at
            or row.reserved_at != receipt.reserved_at
            or row.lease_expires_at != receipt.lease_expires_at
        ):
            raise ValueError("projection")
        return receipt
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_receipt_projection_conflict"
        ) from exc
