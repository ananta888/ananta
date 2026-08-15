"""Transactional authority for transition-owned task-queue reservations.

One reservation is one row, and the row's own constraints are the fence: the
unique effect, fence, attempt and task keys mean a duplicate reservation is
rejected by the database rather than by application logic that a concurrent
process could race past.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import WorkflowTransitionQueueReservationDB
from agent.services.workflow_runtime.queue_reservations import (
    QUEUE_RESERVATION_RECEIPT_SCHEMA,
    WorkflowTransitionQueueReservationConflict,
    WorkflowTransitionQueueReservationError,
    WorkflowTransitionQueueReservationIntent,
    WorkflowTransitionQueueReservationObservation,
    WorkflowTransitionQueueReservationReceipt,
    WorkflowTransitionQueueReservationUnavailable,
    workflow_transition_queue_record_digest,
)
from agent.services.workflow_runtime.sqlalchemy_support import SQLAlchemyStoreSupport


class SQLAlchemyWorkflowTransitionQueueReservationStore(SQLAlchemyStoreSupport):
    """Append-only reservation receipts committed in one transaction."""

    def observe_transition_queue_reservation(
        self,
        *,
        tenant_id: str,
        run_id: str,
        effect_id: str,
    ) -> WorkflowTransitionQueueReservationObservation:
        try:
            with self._transaction() as session:
                row = session.execute(
                    sa.select(WorkflowTransitionQueueReservationDB).where(
                        WorkflowTransitionQueueReservationDB.tenant_id == str(tenant_id),
                        WorkflowTransitionQueueReservationDB.run_id == str(run_id),
                        WorkflowTransitionQueueReservationDB.effect_id == str(effect_id),
                    )
                ).scalar_one_or_none()
                head = session.execute(
                    sa.select(
                        sa.func.coalesce(sa.func.max(WorkflowTransitionQueueReservationDB.reserved_revision), 0)
                    ).where(
                        WorkflowTransitionQueueReservationDB.tenant_id == str(tenant_id),
                        WorkflowTransitionQueueReservationDB.run_id == str(run_id),
                    )
                ).scalar_one()
                return WorkflowTransitionQueueReservationObservation(
                    _receipt_from_row(row) if row is not None else None,
                    int(head or 0),
                )
        except WorkflowTransitionQueueReservationError:
            raise
        except Exception as exc:
            raise WorkflowTransitionQueueReservationUnavailable(
                "workflow_transition_queue_reservation_unavailable"
            ) from exc

    def reserve_transition_queue_slot(
        self,
        intent: WorkflowTransitionQueueReservationIntent,
        *,
        claim_generation: int,
        reserved_at: float,
    ) -> WorkflowTransitionQueueReservationReceipt:
        if not isinstance(intent, WorkflowTransitionQueueReservationIntent):
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_intent_invalid")
        # Validate before touching the database so both adapters reject the
        # same inputs; a bool would otherwise pass as the integer 1.
        generation = _claim_generation(claim_generation)
        moment = _reserved_at(reserved_at)
        existing = self.observe_transition_queue_reservation(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            effect_id=intent.effect_id,
        ).receipt
        if existing is not None:
            # Adoption, not a second reservation: a retry of the same effect
            # must return the receipt it already produced.
            if existing.operation_fence_id != intent.operation_fence_id:
                raise WorkflowTransitionQueueReservationConflict("workflow_transition_queue_reservation_fence_conflict")
            return existing
        try:
            with self._transaction() as session:
                revision = int(
                    session.execute(
                        sa.select(
                            sa.func.coalesce(
                                sa.func.max(WorkflowTransitionQueueReservationDB.reserved_revision),
                                0,
                            )
                        ).where(
                            WorkflowTransitionQueueReservationDB.tenant_id == intent.tenant_id,
                            WorkflowTransitionQueueReservationDB.run_id == intent.run_id,
                        )
                    ).scalar_one()
                    or 0
                )
                receipt = WorkflowTransitionQueueReservationReceipt(
                    schema=QUEUE_RESERVATION_RECEIPT_SCHEMA,
                    receipt_id=intent.receipt_id,
                    transition_id=intent.transition_id,
                    effect_id=intent.effect_id,
                    operation_fence_id=intent.operation_fence_id,
                    attempt_id=intent.attempt_id,
                    task_id=intent.task_id,
                    tenant_id=intent.tenant_id,
                    workflow_id=intent.workflow_id,
                    run_id=intent.run_id,
                    runtime_id=intent.runtime_id,
                    step_id=intent.step_id,
                    queue_intent_digest=intent.queue_intent_digest,
                    reservation_record_digest=workflow_transition_queue_record_digest(
                        {"task_id": intent.task_id, "revision": revision + 1}
                    ),
                    creator_claim_generation=generation,
                    reserved_revision=revision + 1,
                    maximum_retries=intent.maximum_retries,
                    retry_consumed=False,
                    planned_at=intent.planned_at,
                    reserved_at=moment,
                ).with_digest()
                session.add(_row_from_receipt(receipt))
                session.flush()
                return receipt
        except IntegrityError as exc:
            # A constraint fired, so a concurrent writer won the same fence,
            # attempt or task. Re-read rather than guess which.
            adopted = self.observe_transition_queue_reservation(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                effect_id=intent.effect_id,
            ).receipt
            if adopted is not None and adopted.operation_fence_id == intent.operation_fence_id:
                return adopted
            raise WorkflowTransitionQueueReservationConflict("workflow_transition_queue_reservation_conflict") from exc
        except (WorkflowTransitionQueueReservationError, WorkflowTransitionQueueReservationConflict):
            raise
        except Exception as exc:
            raise WorkflowTransitionQueueReservationUnavailable(
                "workflow_transition_queue_reservation_unavailable"
            ) from exc


def _claim_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2_147_483_647:
        raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_claim_generation_invalid")
    return int(value)


def _reserved_at(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_reserved_at_invalid")
    return float(value)


def _row_from_receipt(
    receipt: WorkflowTransitionQueueReservationReceipt,
) -> WorkflowTransitionQueueReservationDB:
    return WorkflowTransitionQueueReservationDB(
        receipt_id=receipt.receipt_id,
        transition_id=receipt.transition_id,
        effect_id=receipt.effect_id,
        operation_fence_id=receipt.operation_fence_id,
        attempt_id=receipt.attempt_id,
        task_id=receipt.task_id,
        tenant_id=receipt.tenant_id,
        workflow_id=receipt.workflow_id,
        run_id=receipt.run_id,
        runtime_id=receipt.runtime_id,
        step_id=receipt.step_id,
        queue_intent_digest=receipt.queue_intent_digest,
        reservation_record_digest=receipt.reservation_record_digest,
        receipt_digest=receipt.receipt_digest,
        creator_claim_generation=receipt.creator_claim_generation,
        reserved_revision=receipt.reserved_revision,
        maximum_retries=receipt.maximum_retries,
        retry_consumed=receipt.retry_consumed,
        planned_at=receipt.planned_at,
        reserved_at=receipt.reserved_at,
        receipt=receipt.to_dict(),
    )


def _receipt_from_row(row: Any) -> WorkflowTransitionQueueReservationReceipt:
    raw = dict(row.receipt or {})
    if not raw:
        raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_receipt_missing")
    return WorkflowTransitionQueueReservationReceipt(**raw)


__all__ = ["SQLAlchemyWorkflowTransitionQueueReservationStore"]
