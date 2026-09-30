"""SQL row mapping and row-locking reads for transition persistence.

Row mappers translate between immutable contracts and ORM rows; the
session access object owns the dialect-aware ``FOR UPDATE`` reads that the
SQL operation collaborators share with the store.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionEffectDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_transition_outbox import (
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionSnapshot,
    thaw_json,
)
from agent.services.workflow_transition_persistence_rules import (
    WorkflowTransitionPersistenceError,
    _mapping_copy,
)


@dataclass(frozen=True)
class SQLTransitionSessions:
    """Narrow unit-of-work port handed from the SQL store to its collaborators.

    The callables are the store's own transaction, read-session and
    ``FOR UPDATE`` seams, so every collaborator shares the store's lock and
    session factory instead of opening an independent lock family.
    """

    transaction: Callable[[], AbstractContextManager[Any]]
    read_session: Callable[[], AbstractContextManager[Any]]
    for_update: Callable[..., Any]

    def locked_transition(
        self,
        session: Any,
        transition_id: str,
    ) -> WorkflowTransitionOutboxDB | None:
        statement = sa.select(WorkflowTransitionOutboxDB).where(WorkflowTransitionOutboxDB.id == str(transition_id))
        return session.execute(self.for_update(statement)).scalar_one_or_none()

    def locked_receipt(
        self,
        session: Any,
        transition: WorkflowTransition,
    ) -> WorkflowControlCommandReceiptDB | None:
        if not transition.receipt_id:
            return None
        statement = sa.select(WorkflowControlCommandReceiptDB).where(
            WorkflowControlCommandReceiptDB.id == transition.receipt_id
        )
        return session.execute(self.for_update(statement)).scalar_one_or_none()

    def locked_binding(
        self,
        session: Any,
        transition: WorkflowTransition,
    ) -> WorkflowControlBindingDB | None:
        statement = sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
        return session.execute(self.for_update(statement)).scalar_one_or_none()


def _transition_row(value: WorkflowTransition) -> WorkflowTransitionOutboxDB:
    return WorkflowTransitionOutboxDB(
        id=value.transition_id,
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        runtime_id=value.runtime_id,
        kind=value.kind,
        request_payload=thaw_json(value.request_payload),
        command_id=value.command_id or None,
        receipt_id=value.receipt_id or None,
        request_fingerprint=value.request_fingerprint,
        admitted_command_digest=value.admitted_command_digest,
        effect_fingerprint=value.effect_fingerprint,
        outcome_fingerprint=value.outcome_fingerprint,
        expected_revision=value.expected_revision,
        expected_checkpoint_ref=value.expected_checkpoint_ref,
        result_status=thaw_json(value.result_status),
        result_checkpoint_ref=value.result_checkpoint_ref,
        state=value.state,
        available_at=value.available_at,
        claim_owner=value.claim_owner,
        claim_generation=value.claim_generation,
        claim_expires_at=value.claim_expires_at,
        last_heartbeat_at=value.last_heartbeat_at,
        attempt_count=value.attempt_count,
        last_error=value.last_error,
        revision=value.revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
    )


def _effect_row(value: WorkflowTransitionEffect) -> WorkflowTransitionEffectDB:
    return WorkflowTransitionEffectDB(
        id=value.effect_id,
        transition_id=value.transition_id,
        ordinal=value.ordinal,
        kind=value.kind,
        idempotency_key=value.idempotency_key,
        payload=thaw_json(value.payload),
        payload_digest=value.payload_digest,
        state=value.state,
        applied_generation=value.applied_generation,
        result_payload=thaw_json(value.result_payload),
        result_digest=value.result_digest,
        revision=value.revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _transition_from_row(row: WorkflowTransitionOutboxDB) -> WorkflowTransition:
    if int(row.attempt_count) != int(row.claim_generation):
        raise WorkflowTransitionPersistenceError("workflow_transition_header_attempt_conflict")
    return WorkflowTransition(
        transition_id=str(row.id),
        tenant_id=str(row.tenant_id),
        workflow_id=str(row.workflow_id),
        run_id=str(row.run_id),
        runtime_id=str(row.runtime_id),
        kind=str(row.kind),
        request_payload=dict(row.request_payload or {}),
        command_id=str(row.command_id or ""),
        receipt_id=str(row.receipt_id or ""),
        request_fingerprint=str(row.request_fingerprint),
        admitted_command_digest=str(row.admitted_command_digest or ""),
        effect_fingerprint=str(row.effect_fingerprint),
        outcome_fingerprint=str(row.outcome_fingerprint or ""),
        expected_revision=int(row.expected_revision),
        expected_checkpoint_ref=str(row.expected_checkpoint_ref),
        result_status=dict(row.result_status or {}),
        result_checkpoint_ref=str(row.result_checkpoint_ref or ""),
        state=str(row.state),
        available_at=float(row.available_at),
        claim_owner=str(row.claim_owner or ""),
        claim_generation=int(row.claim_generation),
        claim_expires_at=float(row.claim_expires_at),
        last_heartbeat_at=float(row.last_heartbeat_at),
        attempt_count=int(row.attempt_count),
        last_error=str(row.last_error or ""),
        revision=int(row.revision),
        created_at=float(row.created_at),
        updated_at=float(row.updated_at),
        completed_at=float(row.completed_at),
    )


def _effect_from_row(row: WorkflowTransitionEffectDB) -> WorkflowTransitionEffect:
    return WorkflowTransitionEffect(
        effect_id=str(row.id),
        transition_id=str(row.transition_id),
        ordinal=int(row.ordinal),
        kind=str(row.kind),
        idempotency_key=str(row.idempotency_key),
        payload=dict(row.payload or {}),
        payload_digest=str(row.payload_digest),
        state=str(row.state),
        applied_generation=int(row.applied_generation),
        result_payload=dict(row.result_payload or {}),
        result_digest=str(row.result_digest or ""),
        revision=int(row.revision),
        created_at=float(row.created_at),
        updated_at=float(row.updated_at),
    )


def _sql_snapshot(session: Any, row: WorkflowTransitionOutboxDB) -> WorkflowTransitionSnapshot:
    effects = (
        session.execute(
            sa.select(WorkflowTransitionEffectDB)
            .where(WorkflowTransitionEffectDB.transition_id == row.id)
            .order_by(WorkflowTransitionEffectDB.ordinal.asc())
        )
        .scalars()
        .all()
    )
    return WorkflowTransitionSnapshot(
        _transition_from_row(row),
        tuple(_effect_from_row(effect) for effect in effects),
    )


def _sql_binding_projection_context(binding: WorkflowControlBindingDB) -> dict[str, Any]:
    return {
        "tenant_id": str(binding.tenant_id),
        "subject_id": str(binding.subject_id),
        "workflow_id": str(binding.workflow_id),
        "run_id": str(binding.run_id),
        "runtime_id": str(binding.runtime_id),
        "plan_hash": str(binding.plan_hash),
        "policy_version": str(binding.policy_version),
        "checkpoint_id": str(binding.checkpoint_id),
        "workflow_request": _mapping_copy(binding.workflow_request),
        "execution_plan": _mapping_copy(binding.execution_plan or {}),
        "last_status": _mapping_copy(binding.last_status or {}),
        "public_status": _mapping_copy(binding.public_status or {}),
        "runtime_revision": int(binding.runtime_revision),
        "runtime_checkpoint_ref": str(binding.runtime_checkpoint_ref),
    }
