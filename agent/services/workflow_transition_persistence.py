"""In-memory and SQL persistence for the Hub workflow transition outbox.

This module stays the public entry point.  Shared fencing and input rules,
SQL row mapping, the in-memory adapter and the SQL lease/effect and
completion collaborators live in ``workflow_transition_persistence_*``
sibling modules.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
)
from agent.services.workflow_transition_outbox import (
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionPublicProjectionPort,
    WorkflowTransitionSnapshot,
    validate_transition_plan,
)
from agent.services.workflow_transition_persistence_memory import InMemoryWorkflowTransitionStore
from agent.services.workflow_transition_persistence_rules import (  # noqa: F401 - compatibility re-exports
    _ACTIVE_MARKER_TRANSITION_STATES,
    _MAX_LEASE_SECONDS,
    _MAX_RESULT_BYTES,
    _RECEIPT_ACTIVE_STATES,
    WorkflowTransitionPersistenceError,
    _assert_binding_execution_state,
    _assert_binding_finalize_state,
    _assert_binding_quarantine_state,
    _assert_effect_begin_order,
    _assert_effect_rejection_safe,
    _assert_effect_stage_attempt,
    _assert_memory_binding_for_stage,
    _assert_memory_receipt_for_stage,
    _assert_owned,
    _assert_receipt_finalize_state,
    _assert_receipt_lease_mirror,
    _assert_sql_binding_for_stage,
    _assert_sql_owned,
    _assert_sql_receipt_for_stage,
    _assert_yield_effect,
    _bounded_mapping,
    _claimable,
    _effect,
    _finalization_values,
    _generation,
    _lease_seconds,
    _limit,
    _linked_receipt,
    _mapping_copy,
    _optional_outcome_fingerprint,
    _owner_id,
    _project_public_status,
    _reason_code,
    _result_payload,
    _retry_at,
    _row_claimable,
    _runtime_matches,
    _same_snapshot_or_raise,
    _status_revision,
)
from agent.services.workflow_transition_persistence_sql_completion import (
    SQLWorkflowTransitionCompletionOperations,
)
from agent.services.workflow_transition_persistence_sql_execution import (
    SQLWorkflowTransitionExecutionOperations,
)
from agent.services.workflow_transition_persistence_sql_rows import (  # noqa: F401 - compatibility re-exports
    SQLTransitionSessions,
    _effect_from_row,
    _effect_row,
    _sql_binding_projection_context,
    _sql_snapshot,
    _transition_from_row,
    _transition_row,
)


class SQLAlchemyWorkflowTransitionStore(SQLAlchemyStoreSupport):
    """Transactional production adapter coupled to binding and receipt proofs.

    The store owns staging and reads; lease/effect progress and terminal
    publication are delegated to collaborators that share this store's
    transaction seam, lock and clock.
    """

    def __init__(
        self,
        bind: Engine | SessionFactory,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
        receipt_projector: WorkflowTransitionPublicProjectionPort | None = None,
    ) -> None:
        super().__init__(bind)
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)
        self._receipt_projector = receipt_projector
        sessions = SQLTransitionSessions(
            transaction=self._transaction,
            read_session=self._read_session,
            for_update=self._for_update,
        )
        self._execution = SQLWorkflowTransitionExecutionOperations(
            sessions=sessions,
            clock=self._clock,
            fault_injector=self._fault_injector,
        )
        self._completion = SQLWorkflowTransitionCompletionOperations(
            sessions=sessions,
            clock=self._clock,
            fault_injector=self._fault_injector,
            receipt_projector=self._receipt_projector,
        )

    def stage(
        self,
        transition: WorkflowTransition,
        effects: Sequence[WorkflowTransitionEffect],
        *,
        receipt_id: str = "",
    ) -> WorkflowTransitionSnapshot:
        values = validate_transition_plan(transition, effects)
        linked_receipt = _linked_receipt(transition, receipt_id)
        now = float(self._clock())
        try:
            with self._transaction() as session:
                existing = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
                if existing is not None:
                    return _same_snapshot_or_raise(
                        _sql_snapshot(session, existing),
                        transition,
                        values,
                    )
                binding = session.get(WorkflowControlBindingDB, transition.workflow_id)
                _assert_sql_binding_for_stage(
                    binding,
                    transition=transition,
                    receipt_id=linked_receipt,
                    now=now,
                )
                receipt = session.get(WorkflowControlCommandReceiptDB, linked_receipt) if linked_receipt else None
                _assert_sql_receipt_for_stage(
                    receipt,
                    transition=transition,
                    now=now,
                )

                transition_row = _transition_row(transition)
                effect_rows = [_effect_row(effect) for effect in values]
                session.add(transition_row)
                session.flush()
                self._fault_injector("stage_after_transition")
                session.add_all(effect_rows)
                session.flush()
                self._fault_injector("stage_after_effects")
                self._fault_injector("stage_before_binding_cas")

                binding_result = session.execute(
                    sa.update(WorkflowControlBindingDB)
                    .where(
                        WorkflowControlBindingDB.id == transition.workflow_id,
                        WorkflowControlBindingDB.tenant_id == transition.tenant_id,
                        WorkflowControlBindingDB.workflow_id == transition.workflow_id,
                        WorkflowControlBindingDB.run_id == transition.run_id,
                        WorkflowControlBindingDB.runtime_id == str(binding.runtime_id),
                        WorkflowControlBindingDB.revision == int(binding.revision),
                        WorkflowControlBindingDB.runtime_revision == transition.expected_revision,
                        WorkflowControlBindingDB.runtime_checkpoint_ref == transition.expected_checkpoint_ref,
                        WorkflowControlBindingDB.active_transition_id == "",
                        WorkflowControlBindingDB.dispatch_intent_id == "",
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_observation_pending.is_(False),
                        WorkflowControlBindingDB.command_receipt_id == linked_receipt,
                        sa.or_(
                            WorkflowControlBindingDB.scheduler_owner == "",
                            WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                        ),
                    )
                    .values(
                        active_transition_id=transition.transition_id,
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
                            WorkflowControlCommandReceiptDB.id == linked_receipt,
                            WorkflowControlCommandReceiptDB.tenant_id == transition.tenant_id,
                            WorkflowControlCommandReceiptDB.workflow_id == transition.workflow_id,
                            WorkflowControlCommandReceiptDB.run_id == transition.run_id,
                            WorkflowControlCommandReceiptDB.expected_revision == transition.expected_revision,
                            WorkflowControlCommandReceiptDB.checkpoint_ref == transition.expected_checkpoint_ref,
                            WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                            WorkflowControlCommandReceiptDB.transition_id == "",
                            WorkflowControlCommandReceiptDB.effect_fingerprint == "",
                            WorkflowControlCommandReceiptDB.outcome_fingerprint == "",
                            sa.or_(
                                WorkflowControlCommandReceiptDB.request_fingerprint == "",
                                WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                            ),
                            WorkflowControlCommandReceiptDB.state == "pending",
                            WorkflowControlCommandReceiptDB.dispatch_owner == "",
                            WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == 0.0,
                            WorkflowControlCommandReceiptDB.dispatch_generation == 0,
                            WorkflowControlCommandReceiptDB.last_heartbeat_at == 0.0,
                        )
                        .values(
                            state="pending",
                            request_fingerprint=transition.request_fingerprint,
                            transition_id=transition.transition_id,
                            effect_fingerprint=transition.effect_fingerprint,
                            dispatch_owner="",
                            dispatch_lease_expires_at=0.0,
                            dispatch_generation=0,
                            last_heartbeat_at=0.0,
                            revision=int(receipt.revision) + 1,
                            updated_at=now,
                        )
                    )
                    if int(receipt_result.rowcount or 0) != 1:
                        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
                self._fault_injector("stage_after_binding_cas")
                return WorkflowTransitionSnapshot(transition, values)
        except IntegrityError as exc:
            existing = self.get(transition.transition_id)
            if existing is not None:
                return _same_snapshot_or_raise(existing, transition, values)
            raise WorkflowTransitionPersistenceError("workflow_transition_stage_conflict") from exc

    def get(self, transition_id: str) -> WorkflowTransitionSnapshot | None:
        with self._read_session() as session:
            row = session.get(WorkflowTransitionOutboxDB, str(transition_id))
            return _sql_snapshot(session, row) if row is not None else None

    def get_active(self, workflow_id: str) -> WorkflowTransitionSnapshot | None:
        with self._read_session() as session:
            binding = session.get(WorkflowControlBindingDB, str(workflow_id))
            transition_id = str((binding.active_transition_id if binding else "") or "")
            if not transition_id:
                return None
            row = session.get(WorkflowTransitionOutboxDB, transition_id)
            if (
                row is None
                or str(row.workflow_id) != str(workflow_id)
                or str(row.state) not in _ACTIVE_MARKER_TRANSITION_STATES
            ):
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_marker_orphaned")
            return _sql_snapshot(session, row)

    def claim(
        self,
        transition_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot | None:
        return self._execution.claim(
            transition_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
        )

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowTransitionSnapshot, ...]:
        return self._execution.claim_due(
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            limit=limit,
        )

    def heartbeat(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot:
        return self._execution.heartbeat(
            transition_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            lease_seconds=lease_seconds,
        )

    def begin_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
    ) -> WorkflowTransitionEffect:
        return self._execution.begin_effect(
            transition_id,
            effect_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
        )

    def finish_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        result_payload: Mapping[str, Any],
        result_digest: str,
    ) -> WorkflowTransitionEffect:
        return self._execution.finish_effect(
            transition_id,
            effect_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            result_payload=result_payload,
            result_digest=result_digest,
        )

    def release(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
        retry_at: float,
    ) -> WorkflowTransitionSnapshot:
        return self._execution.release(
            transition_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            reason_code=reason_code,
            retry_at=retry_at,
        )

    def yield_ready(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        available_at: float,
    ) -> WorkflowTransitionSnapshot:
        return self._execution.yield_ready(
            transition_id,
            effect_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            available_at=available_at,
        )

    def quarantine(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        return self._completion.quarantine(
            transition_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            reason_code=reason_code,
        )

    def reject(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        return self._completion.reject(
            transition_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            reason_code=reason_code,
        )

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
        return self._completion.finalize(
            transition_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            binding_status=binding_status,
            checkpoint_ref=checkpoint_ref,
            finalization_proof=finalization_proof,
            outcome_fingerprint=outcome_fingerprint,
            receipt_result=receipt_result,
        )


__all__ = [
    "InMemoryWorkflowTransitionStore",
    "SQLAlchemyWorkflowTransitionStore",
    "WorkflowTransitionPersistenceError",
]
