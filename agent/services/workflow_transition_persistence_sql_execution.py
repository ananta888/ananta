"""Lease and effect-progress operations of the SQL transition store.

Claim, heartbeat, release, yield and effect begin/finish run under the
store's transaction seam with the unchanged lock order: transition row,
then receipt, then binding, then effect rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

from agent.db_models.workflow_runtime import (
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionEffectDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_READY,
    WorkflowTransitionEffect,
    WorkflowTransitionSnapshot,
    thaw_json,
)
from agent.services.workflow_transition_persistence_rules import (
    WorkflowTransitionPersistenceError,
    _assert_binding_execution_state,
    _assert_effect_begin_order,
    _assert_effect_stage_attempt,
    _assert_owned,
    _assert_receipt_lease_mirror,
    _assert_sql_owned,
    _assert_yield_effect,
    _effect,
    _generation,
    _lease_seconds,
    _limit,
    _owner_id,
    _reason_code,
    _result_payload,
    _retry_at,
    _row_claimable,
)
from agent.services.workflow_transition_persistence_sql_rows import (
    SQLTransitionSessions,
    _effect_from_row,
    _sql_snapshot,
    _transition_from_row,
)


class SQLWorkflowTransitionExecutionOperations:
    """Generation-fenced lease and effect-ledger compare-and-set operations."""

    def __init__(
        self,
        *,
        sessions: SQLTransitionSessions,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)

    def claim(
        self,
        transition_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot | None:
        owner = _owner_id(owner_id)
        lease = _lease_seconds(lease_seconds)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            row = self._sessions.locked_transition(session, transition_id)
            if row is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            if not _row_claimable(row, now=now):
                return None
            current = _transition_from_row(row)
            receipt = (
                session.execute(
                    self._sessions.for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == current.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if current.receipt_id
                else None
            )
            _assert_receipt_lease_mirror(receipt, current)
            expected_revision = int(row.revision)
            next_generation = current.claim_generation + 1
            next_expiry = now + lease
            result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == row.id,
                    WorkflowTransitionOutboxDB.revision == expected_revision,
                    WorkflowTransitionOutboxDB.available_at <= now,
                    sa.or_(
                        WorkflowTransitionOutboxDB.state == TRANSITION_STATE_READY,
                        sa.and_(
                            WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                            WorkflowTransitionOutboxDB.claim_expires_at <= now,
                        ),
                    ),
                )
                .values(
                    state=TRANSITION_STATE_APPLYING,
                    claim_owner=owner,
                    claim_generation=next_generation,
                    claim_expires_at=next_expiry,
                    last_heartbeat_at=now,
                    attempt_count=int(row.attempt_count) + 1,
                    revision=expected_revision + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                return None
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == current.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == current.transition_id,
                        WorkflowControlCommandReceiptDB.state
                        == ("pending" if current.state == TRANSITION_STATE_READY else "dispatching"),
                        WorkflowControlCommandReceiptDB.dispatch_owner == current.claim_owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == current.claim_generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == current.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == current.last_heartbeat_at,
                    )
                    .values(
                        state="dispatching",
                        dispatch_owner=owner,
                        dispatch_generation=next_generation,
                        dispatch_lease_expires_at=next_expiry,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, row.id)
            if refreshed is None:  # pragma: no cover - protected by primary key
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowTransitionSnapshot, ...]:
        bounded = _limit(limit)
        now = float(self._clock())
        with self._sessions.read_session() as session:
            ids = (
                session.execute(
                    sa.select(WorkflowTransitionOutboxDB.id)
                    .where(
                        WorkflowTransitionOutboxDB.available_at <= now,
                        sa.or_(
                            WorkflowTransitionOutboxDB.state == TRANSITION_STATE_READY,
                            sa.and_(
                                WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                                WorkflowTransitionOutboxDB.claim_expires_at <= now,
                            ),
                        ),
                    )
                    .order_by(
                        WorkflowTransitionOutboxDB.available_at.asc(),
                        WorkflowTransitionOutboxDB.created_at.asc(),
                        WorkflowTransitionOutboxDB.id.asc(),
                    )
                    .limit(bounded * 4)
                )
                .scalars()
                .all()
            )
        claimed: list[WorkflowTransitionSnapshot] = []
        for transition_id in ids:
            value = self.claim(
                str(transition_id),
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
            if value is not None:
                claimed.append(value)
                if len(claimed) >= bounded:
                    break
        return tuple(claimed)

    def heartbeat(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot:
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        lease = _lease_seconds(lease_seconds)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            row = self._sessions.locked_transition(session, transition_id)
            if row is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            transition = _transition_from_row(row)
            _assert_owned(
                transition,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
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
            _assert_receipt_lease_mirror(receipt, transition)
            next_expiry = now + lease
            result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == row.id,
                    WorkflowTransitionOutboxDB.revision == int(row.revision),
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    claim_expires_at=next_expiry,
                    last_heartbeat_at=now,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        dispatch_lease_expires_at=next_expiry,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def begin_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
    ) -> WorkflowTransitionEffect:
        now = float(self._clock())
        with self._sessions.transaction() as session:
            transition_row = self._sessions.locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            receipt = self._sessions.locked_receipt(session, transition)
            _assert_receipt_lease_mirror(receipt, transition)
            binding = self._sessions.locked_binding(session, transition)
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
            values = tuple(_effect_from_row(effect) for effect in effect_rows)
            try:
                index, current = _effect(values, str(effect_id))
            except WorkflowTransitionPersistenceError:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            _assert_effect_begin_order(values, effect_index=index)
            _assert_binding_execution_state(binding, transition)
            row = effect_rows[index]
            generation = _generation(claim_generation)
            if any(
                candidate.effect_id != current.effect_id
                and candidate.kind != EFFECT_BINDING_FINALIZE
                and candidate.state in {EFFECT_STATE_APPLYING, EFFECT_STATE_APPLIED}
                and candidate.applied_generation == generation
                for candidate in values
            ):
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_claim_progress_conflict")
            if current.kind == EFFECT_BINDING_FINALIZE:
                raise WorkflowTransitionPersistenceError("workflow_transition_finalize_effect_direct_execution_denied")
            if current.state == EFFECT_STATE_APPLIED:
                return current
            prior_incomplete = session.execute(
                sa.select(WorkflowTransitionEffectDB.id)
                .where(
                    WorkflowTransitionEffectDB.transition_id == str(transition_id),
                    WorkflowTransitionEffectDB.ordinal < current.ordinal,
                    WorkflowTransitionEffectDB.state != EFFECT_STATE_APPLIED,
                )
                .limit(1)
            ).scalar_one_or_none()
            if prior_incomplete is not None:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_order_conflict")
            if current.state == EFFECT_STATE_APPLYING:
                if current.applied_generation >= generation:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            elif current.state != EFFECT_STATE_PLANNED:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_state_conflict")
            result = session.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(
                    WorkflowTransitionEffectDB.id == row.id,
                    WorkflowTransitionEffectDB.transition_id == str(transition_id),
                    WorkflowTransitionEffectDB.revision == int(row.revision),
                    WorkflowTransitionEffectDB.state == current.state,
                    WorkflowTransitionEffectDB.applied_generation == current.applied_generation,
                )
                .values(
                    state=EFFECT_STATE_APPLYING,
                    applied_generation=generation,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionEffectDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            return _effect_from_row(refreshed)

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
        safe_result = _result_payload(result_payload, result_digest=result_digest)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            transition_row = self._sessions.locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner_id,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            receipt = self._sessions.locked_receipt(session, transition)
            _assert_receipt_lease_mirror(receipt, transition)
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
            values = tuple(_effect_from_row(effect) for effect in effect_rows)
            try:
                index, current = _effect(values, str(effect_id))
            except WorkflowTransitionPersistenceError:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            row = effect_rows[index]
            if current.kind == EFFECT_BINDING_FINALIZE:
                raise WorkflowTransitionPersistenceError("workflow_transition_finalize_effect_direct_execution_denied")
            if current.state == EFFECT_STATE_APPLIED:
                if current.result_digest != result_digest or thaw_json(current.result_payload) != safe_result:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_conflict")
                return current
            if current.state != EFFECT_STATE_APPLYING or current.applied_generation != generation:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            _assert_effect_stage_attempt(
                transition,
                values,
                effect_index=index,
                result_payload=safe_result,
            )
            result = session.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(
                    WorkflowTransitionEffectDB.id == row.id,
                    WorkflowTransitionEffectDB.transition_id == str(transition_id),
                    WorkflowTransitionEffectDB.revision == int(row.revision),
                    WorkflowTransitionEffectDB.state == EFFECT_STATE_APPLYING,
                    WorkflowTransitionEffectDB.applied_generation == generation,
                )
                .values(
                    state=EFFECT_STATE_APPLIED,
                    result_payload=safe_result,
                    result_digest=result_digest,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionEffectDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            return _effect_from_row(refreshed)

    def release(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
        retry_at: float,
    ) -> WorkflowTransitionSnapshot:
        reason = _reason_code(reason_code)
        retry = _retry_at(retry_at)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            row = self._sessions.locked_transition(session, transition_id)
            if row is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            transition = _transition_from_row(row)
            _assert_owned(
                transition,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
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
            _assert_receipt_lease_mirror(receipt, transition)
            binding = self._sessions.locked_binding(session, transition)
            _assert_binding_execution_state(binding, transition)
            result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == row.id,
                    WorkflowTransitionOutboxDB.revision == int(row.revision),
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_READY,
                    claim_owner="",
                    claim_expires_at=0.0,
                    available_at=max(now, retry),
                    last_heartbeat_at=now,
                    last_error=reason,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
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
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def yield_ready(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        available_at: float,
    ) -> WorkflowTransitionSnapshot:
        """Yield after one current-generation effect proof in the same UoW."""

        ready_at = _retry_at(available_at)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._sessions.transaction() as session:
            row = self._sessions.locked_transition(session, transition_id)
            _assert_sql_owned(
                row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(row)
            receipt = self._sessions.locked_receipt(session, transition)
            _assert_receipt_lease_mirror(receipt, transition)
            binding = self._sessions.locked_binding(session, transition)
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
            _assert_yield_effect(
                tuple(_effect_from_row(effect) for effect in effect_rows),
                effect_id=effect_id,
                claim_generation=generation,
            )
            _assert_binding_execution_state(binding, transition)

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
                    state=TRANSITION_STATE_READY,
                    claim_owner="",
                    claim_expires_at=0.0,
                    available_at=ready_at,
                    last_heartbeat_at=now,
                    last_error="",
                    revision=transition.revision + 1,
                    updated_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
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
            self._fault_injector("yield_before_commit")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)
