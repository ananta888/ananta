"""Terminal quarantine, rejection and finalization of the SQL transition store.

Each operation publishes the binding, receipt, effect and transition
compare-and-set writes in one transaction, in the original order.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionEffectDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    EFFECT_STATE_REJECTED,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_REJECTED,
    WorkflowTransitionPublicProjectionPort,
    WorkflowTransitionSnapshot,
    thaw_json,
)
from agent.services.workflow_transition_persistence_rules import (
    WorkflowTransitionPersistenceError,
    _assert_binding_finalize_state,
    _assert_binding_quarantine_state,
    _assert_effect_rejection_safe,
    _assert_receipt_finalize_state,
    _assert_sql_owned,
    _finalization_values,
    _generation,
    _owner_id,
    _project_public_status,
    _reason_code,
    _status_revision,
)
from agent.services.workflow_transition_persistence_sql_rows import (
    SQLTransitionSessions,
    _effect_from_row,
    _sql_binding_projection_context,
    _sql_snapshot,
    _transition_from_row,
)


class SQLWorkflowTransitionCompletionOperations:
    """Atomic terminal transitions coupled to binding and receipt proofs."""

    def __init__(
        self,
        *,
        sessions: SQLTransitionSessions,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
        receipt_projector: WorkflowTransitionPublicProjectionPort | None = None,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)
        self._receipt_projector = receipt_projector

    def quarantine(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        """Atomically hold an ambiguous aggregate without changing its effects."""

        reason = _reason_code(reason_code)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            transition_row = self._sessions.locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            binding = session.execute(
                self._sessions.for_update(
                    sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
                )
            ).scalar_one_or_none()
            _assert_binding_quarantine_state(binding, transition)
            receipt = self._sessions.locked_receipt(session, transition)
            _assert_receipt_finalize_state(receipt, transition)

            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="pending",
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
                self._fault_injector("quarantine_after_receipt_cas")

            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_QUARANTINED,
                    claim_owner="",
                    claim_expires_at=0.0,
                    last_heartbeat_at=now,
                    last_error=reason,
                    revision=transition.revision + 1,
                    updated_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            self._fault_injector("quarantine_before_commit")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def reject(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        reason = _reason_code(reason_code)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            transition_row = self._sessions.locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            binding = session.execute(
                self._sessions.for_update(
                    sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
                )
            ).scalar_one_or_none()
            _assert_binding_finalize_state(binding, transition)
            receipt = (
                session.execute(
                    self._sessions.for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == transition.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if transition.receipt_id
                else None
            )
            _assert_receipt_finalize_state(receipt, transition)

            effect_rows = (
                session.execute(
                    self._sessions.for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            _assert_effect_rejection_safe(tuple(_effect_from_row(row) for row in effect_rows))
            for effect_row in effect_rows:
                if effect_row.state not in {
                    EFFECT_STATE_PLANNED,
                    EFFECT_STATE_APPLYING,
                }:
                    continue
                effect_result = session.execute(
                    sa.update(WorkflowTransitionEffectDB)
                    .where(
                        WorkflowTransitionEffectDB.id == effect_row.id,
                        WorkflowTransitionEffectDB.transition_id == transition.transition_id,
                        WorkflowTransitionEffectDB.revision == int(effect_row.revision),
                        WorkflowTransitionEffectDB.state == effect_row.state,
                        WorkflowTransitionEffectDB.applied_generation == int(effect_row.applied_generation),
                    )
                    .values(
                        state=EFFECT_STATE_REJECTED,
                        revision=int(effect_row.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(effect_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_cas_conflict")

            binding_result = session.execute(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == transition.workflow_id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.active_transition_id == transition.transition_id,
                    WorkflowControlBindingDB.runtime_revision == transition.expected_revision,
                    WorkflowControlBindingDB.runtime_checkpoint_ref == transition.expected_checkpoint_ref,
                    WorkflowControlBindingDB.command_receipt_id == transition.receipt_id,
                )
                .values(
                    active_transition_id="",
                    command_receipt_id="" if transition.receipt_id else binding.command_receipt_id,
                    last_transition_id=transition.transition_id,
                    last_transition_command_id=transition.command_id,
                    last_transition_request_fingerprint=transition.request_fingerprint,
                    last_transition_effect_fingerprint=transition.effect_fingerprint,
                    last_transition_outcome_fingerprint="",
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="rejected",
                        rejection_reason=reason,
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")

            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_REJECTED,
                    claim_owner="",
                    claim_expires_at=0.0,
                    last_heartbeat_at=now,
                    last_error=reason,
                    revision=transition.revision + 1,
                    updated_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            self._fault_injector("reject_before_commit")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def finalize(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        binding_status: Mapping[str, Any],
        checkpoint_ref: str,
        finalization_proof: Mapping[str, Any],
        outcome_fingerprint: str = "",
        receipt_result: Mapping[str, Any] | None = None,
    ) -> WorkflowTransitionSnapshot:
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            transition_row = self._sessions.locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            effect_rows = (
                session.execute(
                    self._sessions.for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            effects = tuple(_effect_from_row(row) for row in effect_rows)
            binding = session.execute(
                self._sessions.for_update(
                    sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
                )
            ).scalar_one_or_none()
            _assert_binding_finalize_state(binding, transition)
            receipt = (
                session.execute(
                    self._sessions.for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == transition.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if transition.receipt_id
                else None
            )
            _assert_receipt_finalize_state(receipt, transition)
            public_status = _project_public_status(
                self._receipt_projector,
                transition=transition,
                binding=_sql_binding_projection_context(binding),
                binding_status=binding_status,
                receipt_result=receipt_result,
            )
            status, public_status, completed_outcome, completed_effects = _finalization_values(
                transition,
                effects,
                binding_status=binding_status,
                checkpoint_ref=checkpoint_ref,
                finalization_proof=finalization_proof,
                outcome_fingerprint=outcome_fingerprint,
                public_status=public_status,
                claim_generation=generation,
                now=now,
            )

            self._fault_injector("finalize_before_binding_cas")
            binding_result = session.execute(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == transition.workflow_id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.active_transition_id == transition.transition_id,
                    WorkflowControlBindingDB.runtime_revision == transition.expected_revision,
                    WorkflowControlBindingDB.runtime_checkpoint_ref == transition.expected_checkpoint_ref,
                    WorkflowControlBindingDB.command_receipt_id == transition.receipt_id,
                )
                .values(
                    last_status=status,
                    public_status=public_status,
                    runtime_revision=_status_revision(status),
                    runtime_checkpoint_ref=checkpoint_ref,
                    active_transition_id="",
                    command_receipt_id="" if transition.receipt_id else binding.command_receipt_id,
                    last_transition_id=transition.transition_id,
                    last_transition_command_id=transition.command_id,
                    last_transition_request_fingerprint=transition.request_fingerprint,
                    last_transition_effect_fingerprint=transition.effect_fingerprint,
                    last_transition_outcome_fingerprint=completed_outcome,
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")
            self._fault_injector("finalize_after_binding_cas")

            if receipt is not None:
                receipt_update = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="completed",
                        result_status=public_status,
                        outcome_fingerprint=completed_outcome,
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_update.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
                self._fault_injector("finalize_after_receipt_cas")

            final_row = effect_rows[-1]
            final_effect = completed_effects[-1]
            effect_result = session.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(
                    WorkflowTransitionEffectDB.id == final_row.id,
                    WorkflowTransitionEffectDB.transition_id == transition.transition_id,
                    WorkflowTransitionEffectDB.revision == int(final_row.revision),
                    WorkflowTransitionEffectDB.state == final_row.state,
                    WorkflowTransitionEffectDB.applied_generation == int(final_row.applied_generation),
                )
                .values(
                    state=final_effect.state,
                    applied_generation=final_effect.applied_generation,
                    result_payload=thaw_json(final_effect.result_payload),
                    result_digest=final_effect.result_digest,
                    revision=final_effect.revision,
                    updated_at=final_effect.updated_at,
                )
            )
            if int(effect_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_cas_conflict")

            self._fault_injector("finalize_before_transition_cas")
            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_COMPLETED,
                    result_status=status,
                    result_checkpoint_ref=checkpoint_ref,
                    outcome_fingerprint=completed_outcome,
                    claim_owner="",
                    claim_expires_at=0.0,
                    last_heartbeat_at=now,
                    revision=transition.revision + 1,
                    updated_at=now,
                    completed_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)
