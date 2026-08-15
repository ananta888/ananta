"""Transactional authority for transition-to-checkpoint bindings."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import WorkflowTransitionCheckpointBindingDB
from agent.services.workflow_runtime.checkpoint_bindings import (
    CHECKPOINT_BINDING_RECEIPT_SCHEMA,
    WorkflowTransitionCheckpointBindingConflict,
    WorkflowTransitionCheckpointBindingError,
    WorkflowTransitionCheckpointBindingIntent,
    WorkflowTransitionCheckpointBindingObservation,
    WorkflowTransitionCheckpointBindingReceipt,
    WorkflowTransitionCheckpointBindingUnavailable,
)
from agent.services.workflow_runtime.sqlalchemy_support import SQLAlchemyStoreSupport

_MAX_COUNTER = 2_147_483_647


class SQLAlchemyWorkflowTransitionCheckpointBindingStore(SQLAlchemyStoreSupport):
    """Append-only binding receipts committed in one transaction."""

    def observe_transition_checkpoint_binding(
        self,
        *,
        tenant_id: str,
        run_id: str,
        effect_id: str,
    ) -> WorkflowTransitionCheckpointBindingObservation:
        try:
            with self._transaction() as session:
                row = session.execute(
                    sa.select(WorkflowTransitionCheckpointBindingDB).where(
                        WorkflowTransitionCheckpointBindingDB.tenant_id == str(tenant_id),
                        WorkflowTransitionCheckpointBindingDB.run_id == str(run_id),
                        WorkflowTransitionCheckpointBindingDB.effect_id == str(effect_id),
                    )
                ).scalar_one_or_none()
                head = session.execute(
                    sa.select(
                        sa.func.coalesce(sa.func.max(WorkflowTransitionCheckpointBindingDB.bound_revision), 0)
                    ).where(
                        WorkflowTransitionCheckpointBindingDB.tenant_id == str(tenant_id),
                        WorkflowTransitionCheckpointBindingDB.run_id == str(run_id),
                    )
                ).scalar_one()
                return WorkflowTransitionCheckpointBindingObservation(
                    _receipt_from_row(row) if row is not None else None,
                    int(head or 0),
                )
        except WorkflowTransitionCheckpointBindingError:
            raise
        except Exception as exc:
            raise WorkflowTransitionCheckpointBindingUnavailable(
                "workflow_transition_checkpoint_binding_unavailable"
            ) from exc

    def bind_transition_checkpoint(
        self,
        intent: WorkflowTransitionCheckpointBindingIntent,
        *,
        checkpoint_id: str,
        checkpoint_digest: str,
        bound_revision: int,
        bound_fencing_token: int,
        claim_generation: int,
        bound_at: float,
    ) -> WorkflowTransitionCheckpointBindingReceipt:
        if not isinstance(intent, WorkflowTransitionCheckpointBindingIntent):
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_intent_invalid")
        generation = _counter(claim_generation, "claim_generation")
        revision = _counter(bound_revision, "bound_revision")
        fencing_token = _counter(bound_fencing_token, "bound_fencing_token")
        moment = _moment(bound_at)
        existing = self.observe_transition_checkpoint_binding(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            effect_id=intent.effect_id,
        ).receipt
        if existing is not None:
            if existing.operation_fence_id != intent.operation_fence_id:
                raise WorkflowTransitionCheckpointBindingConflict(
                    "workflow_transition_checkpoint_binding_fence_conflict"
                )
            return existing
        receipt = WorkflowTransitionCheckpointBindingReceipt(
            schema=CHECKPOINT_BINDING_RECEIPT_SCHEMA,
            receipt_id=intent.receipt_id,
            transition_id=intent.transition_id,
            effect_id=intent.effect_id,
            operation_fence_id=intent.operation_fence_id,
            attempt_id=intent.attempt_id,
            checkpoint_id=str(checkpoint_id),
            task_id=intent.task_id,
            tenant_id=intent.tenant_id,
            workflow_id=intent.workflow_id,
            run_id=intent.run_id,
            runtime_id=intent.runtime_id,
            step_id=intent.step_id,
            checkpoint_intent_digest=intent.checkpoint_intent_digest,
            checkpoint_digest=str(checkpoint_digest),
            creator_claim_generation=generation,
            bound_revision=revision,
            bound_fencing_token=fencing_token,
            planned_at=intent.planned_at,
            bound_at=moment,
        ).with_digest()
        try:
            with self._transaction() as session:
                session.add(_row_from_receipt(receipt))
                session.flush()
                return receipt
        except IntegrityError as exc:
            adopted = self.observe_transition_checkpoint_binding(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                effect_id=intent.effect_id,
            ).receipt
            if adopted is not None and adopted.operation_fence_id == intent.operation_fence_id:
                return adopted
            raise WorkflowTransitionCheckpointBindingConflict(
                "workflow_transition_checkpoint_binding_conflict"
            ) from exc
        except (WorkflowTransitionCheckpointBindingError, WorkflowTransitionCheckpointBindingConflict):
            raise
        except Exception as exc:
            raise WorkflowTransitionCheckpointBindingUnavailable(
                "workflow_transition_checkpoint_binding_unavailable"
            ) from exc


def _counter(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_COUNTER:
        raise WorkflowTransitionCheckpointBindingError(f"workflow_transition_checkpoint_binding_{reason}_invalid")
    return int(value)


def _moment(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_bound_at_invalid")
    return float(value)


def _row_from_receipt(
    receipt: WorkflowTransitionCheckpointBindingReceipt,
) -> WorkflowTransitionCheckpointBindingDB:
    return WorkflowTransitionCheckpointBindingDB(
        receipt_id=receipt.receipt_id,
        transition_id=receipt.transition_id,
        effect_id=receipt.effect_id,
        operation_fence_id=receipt.operation_fence_id,
        attempt_id=receipt.attempt_id,
        checkpoint_id=receipt.checkpoint_id,
        task_id=receipt.task_id,
        tenant_id=receipt.tenant_id,
        workflow_id=receipt.workflow_id,
        run_id=receipt.run_id,
        runtime_id=receipt.runtime_id,
        step_id=receipt.step_id,
        checkpoint_intent_digest=receipt.checkpoint_intent_digest,
        checkpoint_digest=receipt.checkpoint_digest,
        receipt_digest=receipt.receipt_digest,
        creator_claim_generation=receipt.creator_claim_generation,
        bound_revision=receipt.bound_revision,
        bound_fencing_token=receipt.bound_fencing_token,
        planned_at=receipt.planned_at,
        bound_at=receipt.bound_at,
        receipt=receipt.to_dict(),
    )


def _receipt_from_row(row: Any) -> WorkflowTransitionCheckpointBindingReceipt:
    raw = dict(row.receipt or {})
    if not raw:
        raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_receipt_missing")
    return WorkflowTransitionCheckpointBindingReceipt(**raw)


__all__ = ["SQLAlchemyWorkflowTransitionCheckpointBindingStore"]
