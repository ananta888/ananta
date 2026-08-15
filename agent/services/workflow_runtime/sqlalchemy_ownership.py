"""SQLAlchemy hub ownership, fencing, and combined retry-budget store."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent.db_models.workflow_runtime import (
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowTransitionOwnershipReservationDB,
)
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.ownership import (
    _OWNERSHIP_MAX_LEGACY_REVISION,
    ExecutionOwnership,
    ExecutionOwnershipStore,
    OwnershipClaim,
    RetryBudgetSnapshot,
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
    WorkflowTransitionOwnershipRetryConsumption,
    _assert_expected_revision,
    _assert_owner,
    _heartbeat,
    _timestamp,
    _transition_ownership_evidence,
    _transition_ownership_observation,
    _transition_ownership_reservation_values,
    _validate_lease,
)
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)
from ananta_contracts.hub_task_gateway import RETRY_CATEGORIES


class SQLAlchemyExecutionOwnershipStore(SQLAlchemyStoreSupport, ExecutionOwnershipStore):
    """Distributed lease store with database CAS and immutable attempt history."""

    def __init__(self, bind: Engine | SessionFactory) -> None:
        super().__init__(bind)

    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        values = {
            "tenant_id": str(tenant_id),
            "workflow_id": str(workflow_id),
            "run_id": str(run_id),
            "step_id": str(step_id),
            "owner_id": str(owner_id),
        }
        try:
            return self._claim_once(
                values,
                timestamp=timestamp,
                lease_seconds=float(lease_seconds),
                maximum_retries=int(maximum_retries),
            )
        except IntegrityError:
            # An absent-row race is resolved by re-reading the committed winner.
            return self._claim_once(
                values,
                timestamp=timestamp,
                lease_seconds=float(lease_seconds),
                maximum_retries=int(maximum_retries),
            )

    def _claim_once(
        self,
        values: dict[str, str],
        *,
        timestamp: float,
        lease_seconds: float,
        maximum_retries: int,
    ) -> OwnershipClaim:
        with self._transaction() as session:
            row = self._read_row(
                session,
                tenant_id=values["tenant_id"],
                run_id=values["run_id"],
                step_id=values["step_id"],
                lock=True,
            )
            current = _ownership(row) if row is not None else None
            if current is not None and current.workflow_id != values["workflow_id"]:
                raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
            if current is not None and current.status == "completed":
                return OwnershipClaim(current, False, "already_completed")
            if current is not None and current.status == "active" and current.lease_expires_at > timestamp:
                reason = "already_owned" if current.owner_id == values["owner_id"] else "lease_held"
                return OwnershipClaim(current, False, reason)

            attempt_id = f"att-{uuid.uuid4().hex}"
            if current is not None:
                self._consume_retry_in_session(
                    session,
                    tenant_id=values["tenant_id"],
                    run_id=values["run_id"],
                    retry_id=attempt_id,
                    category="hub_task",
                    maximum=maximum_retries,
                )
            ownership = ExecutionOwnership(
                tenant_id=values["tenant_id"],
                workflow_id=values["workflow_id"],
                run_id=values["run_id"],
                step_id=values["step_id"],
                attempt_id=attempt_id,
                owner_id=values["owner_id"],
                fencing_token=(current.fencing_token + 1) if current else 1,
                revision=(current.revision + 1) if current else 1,
                status="active",
                lease_expires_at=timestamp + lease_seconds,
                last_heartbeat_at=timestamp,
            )
            ownership.assert_valid()
            self._write(session, row=row, value=ownership)
            return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")

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
            self._read_row(
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
            self._transition_reservation_fault("before_retry", consumption)
            if consumption is not None:
                self._consume_transition_retry_in_session(
                    session,
                    intent=intent,
                    observation=observation,
                    consumption=consumption,
                    budget=budget,
                    reserved_at=reserved_at,
                )
            self._transition_reservation_fault("after_retry", consumption)
            current_row = self._read_row(
                session,
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
                lock=True,
            )
            current = _ownership_exact(current_row) if current_row is not None else None
            if current != observation.current:
                raise OptimisticConcurrencyError("workflow_transition_ownership_current_compare_and_set_failed")
            self._write(session, row=current_row, value=acquired)
            self._transition_reservation_fault("after_current", acquired)
            self._transition_reservation_fault("after_history", acquired)
            session.add(_transition_receipt_row(receipt))
            session.flush()
            self._transition_reservation_fault("after_receipt", receipt)
            self._transition_reservation_fault("before_commit", receipt)
        self._transition_reservation_fault("after_commit", receipt)
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
        current_row = self._read_row(
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
            current_again_row = self._read_row(
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
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_without_receipt")
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

    def _transition_reservation_fault(self, stage: str, value: object) -> None:
        del stage, value

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        return self._mutate_owned(
            values,
            lambda current: _heartbeat(current, values=values, timestamp=timestamp),
        )

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._transaction() as session:
            row, current = self._required_owned(session, values)
            if current.status == "completed" and current.result_ack_key == result_ack_key:
                return current
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active" or current.lease_expires_at <= timestamp:
                raise FencingTokenError("result_owner_not_active")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="completed",
                result_ack_key=result_ack_key,
                lease_expires_at=timestamp,
            )
            self._write(session, row=row, value=updated)
            return updated

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        def mutate(current: ExecutionOwnership) -> ExecutionOwnership:
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            return replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )

        return self._mutate_owned(values, mutate)

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(time.time() if now is None else now)
        with self._transaction() as session:
            row = self._read_row(
                session,
                tenant_id=str(tenant_id),
                run_id=str(run_id),
                step_id=str(step_id),
                lock=True,
            )
            if row is None:
                return None
            current = _ownership(row)
            if current.status != "active" or current.lease_expires_at > timestamp:
                return None
            updated = replace(
                current,
                revision=current.revision + 1,
                status="orphaned",
                failure_code="lease_expired",
            )
            self._write(session, row=row, value=updated)
            return updated

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._read_session() as session:
            row = self._read_row(
                session,
                tenant_id=str(tenant_id),
                run_id=str(run_id),
                step_id=str(step_id),
                lock=False,
            )
            return _ownership(row) if row is not None else None

    def list_history(self, *, tenant_id: str, run_id: str, step_id: str) -> tuple[ExecutionOwnership, ...]:
        with self._read_session() as session:
            rows = (
                session.execute(
                    sa.select(WorkflowExecutionAttemptHistoryDB)
                    .where(
                        WorkflowExecutionAttemptHistoryDB.tenant_id == str(tenant_id),
                        WorkflowExecutionAttemptHistoryDB.run_id == str(run_id),
                        WorkflowExecutionAttemptHistoryDB.step_id == str(step_id),
                    )
                    .order_by(WorkflowExecutionAttemptHistoryDB.revision.asc())
                )
                .scalars()
                .all()
            )
            return tuple(ExecutionOwnership.from_mapping(dict(row.ownership)) for row in rows)

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        try:
            with self._transaction() as session:
                return self._consume_retry_in_session(
                    session,
                    tenant_id=str(tenant_id),
                    run_id=str(run_id),
                    retry_id=str(retry_id),
                    category=str(category),
                    maximum=int(maximum),
                )
        except IntegrityError:
            # Resolve a concurrent budget/consumption insert through the same
            # idempotent operation after the winner commits.
            with self._transaction() as session:
                return self._consume_retry_in_session(
                    session,
                    tenant_id=str(tenant_id),
                    run_id=str(run_id),
                    retry_id=str(retry_id),
                    category=str(category),
                    maximum=int(maximum),
                )

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        budget_id = stable_row_id("wfrb", tenant_id, run_id)
        with self._read_session() as session:
            row = session.get(WorkflowRetryBudgetDB, budget_id)
            if row is not None and row.maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
            return RetryBudgetSnapshot(
                str(tenant_id),
                str(run_id),
                used=int(row.used if row is not None else 0),
                maximum=int(maximum),
            )

    def _consume_retry_in_session(
        self,
        session,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        consumption_id = stable_row_id("wfrr", tenant_id, run_id, retry_id)
        budget_id = stable_row_id("wfrb", tenant_id, run_id)
        duplicate = session.get(WorkflowRetryConsumptionDB, consumption_id)
        statement = sa.select(WorkflowRetryBudgetDB).where(WorkflowRetryBudgetDB.id == budget_id)
        budget = session.execute(self._for_update(statement)).scalar_one_or_none()
        used = int(budget.used if budget is not None else 0)
        if budget is not None and budget.maximum != maximum:
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        if duplicate is not None and duplicate.category != category:
            raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
        if duplicate is not None:
            return RetryBudgetSnapshot(tenant_id, run_id, used=used, maximum=maximum)
        if used >= maximum:
            raise InvalidTransitionError("retry_budget_exhausted")

        timestamp = time.time()
        session.add(
            WorkflowRetryConsumptionDB(
                id=consumption_id,
                tenant_id=tenant_id,
                run_id=run_id,
                retry_id=retry_id,
                category=category,
                consumed_at=timestamp,
            )
        )
        if budget is None:
            session.add(
                WorkflowRetryBudgetDB(
                    id=budget_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    used=1,
                    maximum=maximum,
                    revision=1,
                    updated_at=timestamp,
                )
            )
        else:
            result = session.execute(
                sa.update(WorkflowRetryBudgetDB)
                .where(
                    WorkflowRetryBudgetDB.id == budget.id,
                    WorkflowRetryBudgetDB.revision == budget.revision,
                    WorkflowRetryBudgetDB.used == budget.used,
                )
                .values(
                    used=budget.used + 1,
                    revision=budget.revision + 1,
                    updated_at=timestamp,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise OptimisticConcurrencyError("retry_budget_compare_and_set_failed")
        session.flush()
        return RetryBudgetSnapshot(tenant_id, run_id, used=used + 1, maximum=maximum)

    def _mutate_owned(
        self,
        values: dict[str, Any],
        mutate: Callable[[ExecutionOwnership], ExecutionOwnership],
    ) -> ExecutionOwnership:
        with self._transaction() as session:
            row, current = self._required_owned(session, values)
            updated = mutate(current)
            self._write(session, row=row, value=updated)
            return updated

    def _required_owned(self, session, values: dict[str, Any]):
        row = self._read_row(
            session,
            tenant_id=str(values["tenant_id"]),
            run_id=str(values["run_id"]),
            step_id=str(values["step_id"]),
            lock=True,
        )
        if row is None:
            raise KeyError("execution_ownership_not_found")
        current = _ownership(row)
        _assert_owner(
            current,
            attempt_id=str(values["attempt_id"]),
            owner_id=str(values["owner_id"]),
            fencing_token=int(values["fencing_token"]),
        )
        return row, current

    def _read_row(
        self,
        session,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        lock: bool,
    ) -> WorkflowExecutionOwnershipDB | None:
        statement = sa.select(WorkflowExecutionOwnershipDB).where(
            WorkflowExecutionOwnershipDB.tenant_id == tenant_id,
            WorkflowExecutionOwnershipDB.run_id == run_id,
            WorkflowExecutionOwnershipDB.step_id == step_id,
        )
        if lock:
            statement = self._for_update(statement)
        return session.execute(statement).scalar_one_or_none()

    @staticmethod
    def _write(
        session,
        *,
        row: WorkflowExecutionOwnershipDB | None,
        value: ExecutionOwnership,
    ) -> None:
        value.assert_valid()
        if row is None:
            session.add(_ownership_row(value))
        else:
            result = session.execute(
                sa.update(WorkflowExecutionOwnershipDB)
                .where(
                    WorkflowExecutionOwnershipDB.id == row.id,
                    WorkflowExecutionOwnershipDB.revision == row.revision,
                    WorkflowExecutionOwnershipDB.fencing_token == row.fencing_token,
                )
                .values(
                    workflow_id=value.workflow_id,
                    attempt_id=value.attempt_id,
                    owner_id=value.owner_id,
                    status=value.status,
                    revision=value.revision,
                    fencing_token=value.fencing_token,
                    lease_expires_at=value.lease_expires_at,
                    last_heartbeat_at=value.last_heartbeat_at,
                    ownership=value.to_dict(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise OptimisticConcurrencyError("execution_ownership_compare_and_set_failed")
        session.add(_history_row(value))
        session.flush()


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
