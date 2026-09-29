"""Transition-effect ownership reservations of the SQLAlchemy ownership store.

``SQLAlchemyTransitionReservations`` is a collaborator composed by
``SQLAlchemyExecutionOwnershipStore``.  It receives the store's transaction and
read-session factories, its row-lock policy, the ownership row port, and a
fault hook, and keeps the reservation protocol in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy import Select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

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
from agent.services.workflow_runtime.errors import (
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.ownership import (
    _OWNERSHIP_MAX_LEGACY_REVISION,
    ExecutionOwnership,
    RetryBudgetSnapshot,
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
    WorkflowTransitionOwnershipRetryConsumption,
    _transition_ownership_evidence,
    _transition_ownership_observation,
    _transition_ownership_reservation_values,
)
from agent.services.workflow_runtime.sqlalchemy_ownership_rows import (
    _history_exact,
    _ownership_exact,
    _retry_budget_exact,
    _retry_consumption_exact,
    _transition_receipt,
    _transition_receipt_row,
)


class OwnershipRowPort(Protocol):
    """Current-row read and compare-and-set used inside a caller-owned session."""

    def read(
        self,
        session: Session,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        lock: bool,
    ) -> WorkflowExecutionOwnershipDB | None: ...

    def write(
        self,
        session: Session,
        *,
        row: WorkflowExecutionOwnershipDB | None,
        value: ExecutionOwnership,
    ) -> None: ...


TransitionReservationFault = Callable[[str, object], None]


class SQLAlchemyTransitionReservations:
    """Observe, read and atomically commit transition-effect ownership reservations."""

    def __init__(
        self,
        *,
        transaction: Callable[[], AbstractContextManager[Session]],
        read_session: Callable[[], AbstractContextManager[Session]],
        for_update: Callable[[Select[Any]], Select[Any]],
        rows: OwnershipRowPort,
        fault: TransitionReservationFault,
    ) -> None:
        self._transaction = transaction
        self._read_session = read_session
        self._for_update = for_update
        self._rows = rows
        self._fault = fault

    def observe_transition_reservation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        try:
            with self._transaction() as session:
                return self._transition_reservation_observation(
                    session,
                    intent,
                    claim_generation=claim_generation,
                    lock=True,
                    commit_attempt=False,
                )
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except SQLAlchemyError as exc:
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_read_unavailable"
            ) from exc

    def read_transition_reservation_history(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        try:
            with self._read_session() as session:
                return self._transition_reservation_history(session, intent, lock=False)
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except SQLAlchemyError as exc:
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_history_unavailable"
            ) from exc

    def _transition_reservation_history(
        self,
        session,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        lock: bool,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        receipt_rows = self._transition_receipt_alias_rows(
            session,
            intent,
            current=None,
            lock=lock,
            include_prospective=False,
        )
        receipts = tuple(_transition_receipt(row) for row in receipt_rows)
        distinct = {value.receipt_digest: value for value in receipts}
        history_statement = sa.select(WorkflowExecutionAttemptHistoryDB).where(
            WorkflowExecutionAttemptHistoryDB.tenant_id == intent.tenant_id,
            WorkflowExecutionAttemptHistoryDB.run_id == intent.run_id,
            WorkflowExecutionAttemptHistoryDB.step_id == intent.step_id,
        )
        if len(distinct) == 1:
            receipt = next(iter(distinct.values()))
            revisions = [receipt.acquired_revision]
            if receipt.prior_ownership is not None:
                revisions.append(receipt.prior_ownership.revision)
            history_statement = history_statement.where(WorkflowExecutionAttemptHistoryDB.revision.in_(revisions))
        else:
            history_statement = history_statement.where(
                WorkflowExecutionAttemptHistoryDB.attempt_id == intent.attempt_id
            ).limit(2)
        history_rows = (
            session.execute(history_statement.order_by(WorkflowExecutionAttemptHistoryDB.revision.asc()))
            .scalars()
            .all()
        )
        consumption_row = self._transition_retry_consumption_row(
            session,
            intent,
            lock=lock,
        )
        if not receipts and (history_rows or consumption_row is not None):
            receipts_again = tuple(
                _transition_receipt(row)
                for row in self._transition_receipt_alias_rows(
                    session,
                    intent,
                    current=None,
                    lock=lock,
                    include_prospective=False,
                )
            )
            if receipts_again:
                receipts = receipts_again
                distinct = {value.receipt_digest: value for value in receipts}
                history_statement = sa.select(WorkflowExecutionAttemptHistoryDB).where(
                    WorkflowExecutionAttemptHistoryDB.tenant_id == intent.tenant_id,
                    WorkflowExecutionAttemptHistoryDB.run_id == intent.run_id,
                    WorkflowExecutionAttemptHistoryDB.step_id == intent.step_id,
                )
                if len(distinct) == 1:
                    receipt = next(iter(distinct.values()))
                    revisions = [receipt.acquired_revision]
                    if receipt.prior_ownership is not None:
                        revisions.append(receipt.prior_ownership.revision)
                    history_statement = history_statement.where(
                        WorkflowExecutionAttemptHistoryDB.revision.in_(revisions)
                    )
                else:
                    history_statement = history_statement.where(
                        WorkflowExecutionAttemptHistoryDB.attempt_id == intent.attempt_id
                    ).limit(2)
                history_rows = (
                    session.execute(history_statement.order_by(WorkflowExecutionAttemptHistoryDB.revision.asc()))
                    .scalars()
                    .all()
                )
        return _transition_ownership_evidence(
            intent,
            history=tuple(_history_exact(row) for row in history_rows),
            retry_consumption=(
                None if consumption_row is None else _retry_consumption_exact(consumption_row, intent=intent)
            ),
            receipts=receipts,
        )

    def reserve_transition_effect(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        try:
            return self._reserve_transition_effect_once(
                intent,
                creator_claim_generation=creator_claim_generation,
                expected_observation_digest=expected_observation_digest,
                reserved_at=reserved_at,
            )
        except WorkflowTransitionOwnershipReservationStale:
            raise
        except (IntegrityError, OptimisticConcurrencyError):
            try:
                return self._resolve_transition_reservation_winner(
                    intent,
                    creator_claim_generation=creator_claim_generation,
                )
            except WorkflowTransitionOwnershipReservationConflict:
                raise
            except SQLAlchemyError as exc:
                raise WorkflowTransitionOwnershipReservationUnavailable(
                    "workflow_transition_ownership_commit_unavailable"
                ) from exc
        except SQLAlchemyError as exc:
            try:
                evidence = self.read_transition_reservation_history(intent)
            except WorkflowTransitionOwnershipReservationConflict:
                raise
            except WorkflowTransitionOwnershipReservationUnavailable as read_exc:
                raise WorkflowTransitionOwnershipReservationUnavailable(
                    "workflow_transition_ownership_commit_unavailable"
                ) from read_exc
            if evidence.receipt is not None and evidence.receipt.creator_claim_generation <= creator_claim_generation:
                return evidence.receipt
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_commit_unavailable"
            ) from exc

    def _reserve_transition_effect_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        with self._transaction() as session:
            self._rows.read(
                session,
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
                lock=True,
            )
            evidence = self._transition_reservation_history(session, intent, lock=True)
            if evidence.receipt is not None:
                if evidence.receipt.creator_claim_generation > creator_claim_generation:
                    raise WorkflowTransitionOwnershipReservationConflict(
                        "workflow_transition_ownership_receipt_generation_conflict"
                    )
                return evidence.receipt
            observation = self._transition_reservation_observation(
                session,
                intent,
                claim_generation=creator_claim_generation,
                lock=True,
                commit_attempt=True,
            )
            acquired, consumption, budget, receipt = _transition_ownership_reservation_values(
                observation,
                creator_claim_generation=creator_claim_generation,
                expected_observation_digest=expected_observation_digest,
                reserved_at=reserved_at,
            )
            if observation.receipt is not None:
                return observation.receipt
            self._fault("before_retry", consumption)
            if consumption is not None:
                self._consume_transition_retry_in_session(
                    session,
                    intent=intent,
                    observation=observation,
                    consumption=consumption,
                    budget=budget,
                    reserved_at=reserved_at,
                )
            self._fault("after_retry", consumption)
            current_row = self._rows.read(
                session,
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
                lock=True,
            )
            current = _ownership_exact(current_row) if current_row is not None else None
            if current != observation.current:
                raise OptimisticConcurrencyError("workflow_transition_ownership_current_compare_and_set_failed")
            self._rows.write(session, row=current_row, value=acquired)
            self._fault("after_current", acquired)
            self._fault("after_history", acquired)
            session.add(_transition_receipt_row(receipt))
            session.flush()
            self._fault("after_receipt", receipt)
            self._fault("before_commit", receipt)
        self._fault("after_commit", receipt)
        return receipt

    def _resolve_transition_reservation_winner(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        evidence = self.read_transition_reservation_history(intent)
        if evidence.receipt is not None:
            if evidence.receipt.creator_claim_generation > creator_claim_generation:
                raise WorkflowTransitionOwnershipReservationConflict(
                    "workflow_transition_ownership_receipt_generation_conflict"
                )
            return evidence.receipt
        observation = self.observe_transition_reservation(
            intent,
            claim_generation=creator_claim_generation,
        )
        if observation.receipt is not None:
            return observation.receipt
        raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_commit_stale")

    def _transition_reservation_observation(
        self,
        session,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
        lock: bool,
        commit_attempt: bool,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        current_row = self._rows.read(
            session,
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            step_id=intent.step_id,
            lock=lock,
        )
        current = _ownership_exact(current_row) if current_row is not None else None
        receipt_rows = self._transition_receipt_alias_rows(
            session,
            intent,
            current=current,
            lock=lock,
            include_prospective=True,
        )
        receipts = tuple(_transition_receipt(row) for row in receipt_rows)
        anchor_revisions = {
            revision
            for receipt in receipts
            for revision in (
                receipt.acquired_revision,
                receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
            )
            if revision > 0
        }
        if current is not None:
            anchor_revisions.add(current.revision)
        base_history = sa.select(WorkflowExecutionAttemptHistoryDB).where(
            WorkflowExecutionAttemptHistoryDB.tenant_id == intent.tenant_id,
            WorkflowExecutionAttemptHistoryDB.run_id == intent.run_id,
            WorkflowExecutionAttemptHistoryDB.step_id == intent.step_id,
        )
        selected_history: dict[str, WorkflowExecutionAttemptHistoryDB] = {}
        if anchor_revisions:
            for row in session.execute(
                base_history.where(WorkflowExecutionAttemptHistoryDB.revision.in_(anchor_revisions))
            ).scalars():
                selected_history[row.id] = row
        if not receipts:
            for row in session.execute(
                base_history.where(WorkflowExecutionAttemptHistoryDB.attempt_id == intent.attempt_id).limit(2)
            ).scalars():
                selected_history[row.id] = row
        if current is None:
            latest = session.execute(
                base_history.order_by(WorkflowExecutionAttemptHistoryDB.revision.desc()).limit(1)
            ).scalar_one_or_none()
            if latest is not None:
                selected_history[latest.id] = latest
        history_rows = sorted(selected_history.values(), key=lambda value: value.revision)
        history = tuple(_history_exact(row) for row in history_rows)
        consumption_row = self._transition_retry_consumption_row(
            session,
            intent,
            lock=lock,
        )
        consumption = None if consumption_row is None else _retry_consumption_exact(consumption_row, intent=intent)
        budget_row = self._transition_retry_budget_row(session, intent, lock=lock)
        budget = _retry_budget_exact(budget_row, intent=intent)
        if lock:
            # Under READ COMMITTED an absent-row race cannot be gap-locked.  A
            # second exact read detects any winner before this snapshot grants
            # mutation authority.
            current_again_row = self._rows.read(
                session,
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
                lock=True,
            )
            current_again = _ownership_exact(current_again_row) if current_again_row is not None else None
            receipts_again = tuple(
                _transition_receipt(row)
                for row in self._transition_receipt_alias_rows(
                    session,
                    intent,
                    current=current_again,
                    lock=True,
                    include_prospective=True,
                )
            )
            consumption_again_row = self._transition_retry_consumption_row(
                session,
                intent,
                lock=True,
            )
            consumption_again = (
                None
                if consumption_again_row is None
                else _retry_consumption_exact(consumption_again_row, intent=intent)
            )
            budget_again = self._transition_retry_budget_row(session, intent, lock=True)
            if (
                current_again != current
                or receipts_again != receipts
                or consumption_again != consumption
                or _retry_budget_exact(budget_again, intent=intent) != budget
            ):
                if commit_attempt:
                    raise OptimisticConcurrencyError("workflow_transition_ownership_snapshot_changed")
                raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_snapshot_changed")
        return _transition_ownership_observation(
            intent,
            claim_generation=claim_generation,
            current=current,
            history=history,
            retry_consumption=consumption,
            retry_budget=budget,
            receipts=receipts,
        )

    def _transition_receipt_alias_rows(
        self,
        session,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        current: ExecutionOwnership | None,
        lock: bool,
        include_prospective: bool,
    ) -> tuple[WorkflowTransitionOwnershipReservationDB, ...]:
        clauses = [
            WorkflowTransitionOwnershipReservationDB.receipt_id == intent.receipt_id,
            WorkflowTransitionOwnershipReservationDB.effect_id == intent.effect_id,
            WorkflowTransitionOwnershipReservationDB.operation_fence_id == intent.operation_fence_id,
            WorkflowTransitionOwnershipReservationDB.attempt_id == intent.attempt_id,
        ]
        if current is not None:
            clauses.extend(
                [
                    WorkflowTransitionOwnershipReservationDB.attempt_id == current.attempt_id,
                    sa.and_(
                        WorkflowTransitionOwnershipReservationDB.tenant_id == current.tenant_id,
                        WorkflowTransitionOwnershipReservationDB.run_id == current.run_id,
                        WorkflowTransitionOwnershipReservationDB.step_id == current.step_id,
                        sa.or_(
                            WorkflowTransitionOwnershipReservationDB.acquired_revision == current.revision,
                            WorkflowTransitionOwnershipReservationDB.acquired_fencing_token == current.fencing_token,
                        ),
                    ),
                ]
            )
        if include_prospective and current is None:
            clauses.append(
                sa.and_(
                    WorkflowTransitionOwnershipReservationDB.tenant_id == intent.tenant_id,
                    WorkflowTransitionOwnershipReservationDB.run_id == intent.run_id,
                    WorkflowTransitionOwnershipReservationDB.step_id == intent.step_id,
                )
            )
        elif include_prospective and (
            current is not None
            and current.revision < _OWNERSHIP_MAX_LEGACY_REVISION
            and current.fencing_token < _OWNERSHIP_MAX_LEGACY_REVISION
        ):
            next_revision = current.revision + 1
            next_fencing_token = current.fencing_token + 1
            clauses.append(
                sa.and_(
                    WorkflowTransitionOwnershipReservationDB.tenant_id == intent.tenant_id,
                    WorkflowTransitionOwnershipReservationDB.run_id == intent.run_id,
                    WorkflowTransitionOwnershipReservationDB.step_id == intent.step_id,
                    sa.or_(
                        WorkflowTransitionOwnershipReservationDB.acquired_revision >= next_revision,
                        WorkflowTransitionOwnershipReservationDB.acquired_fencing_token >= next_fencing_token,
                    ),
                )
            )
        statement = (
            sa.select(WorkflowTransitionOwnershipReservationDB)
            .where(sa.or_(*clauses))
            .order_by(WorkflowTransitionOwnershipReservationDB.receipt_id)
            .limit(17)
        )
        if lock:
            statement = self._for_update(statement)
        return tuple(session.execute(statement).scalars().all())

    def _transition_retry_consumption_row(
        self,
        session,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        lock: bool,
    ) -> WorkflowRetryConsumptionDB | None:
        expected_id = stable_row_id("wfrr", intent.tenant_id, intent.run_id, intent.retry_id)
        statement = (
            sa.select(WorkflowRetryConsumptionDB)
            .where(
                sa.or_(
                    WorkflowRetryConsumptionDB.id == expected_id,
                    sa.and_(
                        WorkflowRetryConsumptionDB.tenant_id == intent.tenant_id,
                        WorkflowRetryConsumptionDB.run_id == intent.run_id,
                        WorkflowRetryConsumptionDB.retry_id == intent.retry_id,
                    ),
                )
            )
            .order_by(WorkflowRetryConsumptionDB.id)
            .limit(2)
        )
        if lock:
            statement = self._for_update(statement)
        rows = tuple(session.execute(statement).scalars().all())
        if len(rows) > 1:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_consumption_alias_conflict"
            )
        row = rows[0] if rows else None
        if row is not None:
            _retry_consumption_exact(row, intent=intent)
        return row

    def _transition_retry_budget_row(
        self,
        session,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        lock: bool,
    ) -> WorkflowRetryBudgetDB | None:
        expected_id = stable_row_id("wfrb", intent.tenant_id, intent.run_id)
        statement = (
            sa.select(WorkflowRetryBudgetDB)
            .where(
                sa.or_(
                    WorkflowRetryBudgetDB.id == expected_id,
                    sa.and_(
                        WorkflowRetryBudgetDB.tenant_id == intent.tenant_id,
                        WorkflowRetryBudgetDB.run_id == intent.run_id,
                    ),
                )
            )
            .order_by(WorkflowRetryBudgetDB.id)
            .limit(2)
        )
        if lock:
            statement = self._for_update(statement)
        rows = tuple(session.execute(statement).scalars().all())
        if len(rows) > 1:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_budget_alias_conflict"
            )
        row = rows[0] if rows else None
        _retry_budget_exact(row, intent=intent)
        return row

    def _consume_transition_retry_in_session(
        self,
        session,
        *,
        intent: WorkflowTransitionOwnershipReservationIntent,
        observation: WorkflowTransitionOwnershipReservationObservation,
        consumption: WorkflowTransitionOwnershipRetryConsumption,
        budget: RetryBudgetSnapshot,
        reserved_at: float,
    ) -> None:
        consumption_id = stable_row_id("wfrr", intent.tenant_id, intent.run_id, intent.retry_id)
        existing_consumption = self._transition_retry_consumption_row(session, intent, lock=True)
        if existing_consumption is not None:
            raise OptimisticConcurrencyError("workflow_transition_ownership_retry_snapshot_changed")
        session.add(
            WorkflowRetryConsumptionDB(
                id=consumption_id,
                tenant_id=consumption.tenant_id,
                run_id=consumption.run_id,
                retry_id=consumption.retry_id,
                category=consumption.category,
                consumed_at=reserved_at,
            )
        )
        budget_id = stable_row_id("wfrb", intent.tenant_id, intent.run_id)
        budget_row = self._transition_retry_budget_row(session, intent, lock=True)
        if budget_row is None:
            if observation.retry_budget.used != 0:
                raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_retry_budget_conflict")
            session.add(
                WorkflowRetryBudgetDB(
                    id=budget_id,
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                    used=budget.used,
                    maximum=budget.maximum,
                    revision=1,
                    updated_at=reserved_at,
                )
            )
        else:
            result = session.execute(
                sa.update(WorkflowRetryBudgetDB)
                .where(
                    WorkflowRetryBudgetDB.id == budget_row.id,
                    WorkflowRetryBudgetDB.used == observation.retry_budget.used,
                    WorkflowRetryBudgetDB.maximum == intent.maximum_retries,
                    WorkflowRetryBudgetDB.revision == budget_row.revision,
                )
                .values(
                    used=budget.used,
                    revision=budget_row.revision + 1,
                    updated_at=reserved_at,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise OptimisticConcurrencyError("workflow_transition_ownership_retry_budget_cas_failed")
        session.flush()
