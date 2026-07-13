"""SQLAlchemy hub ownership, fencing, and combined retry-budget store."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any, Callable

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
)
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.ownership import (
    ExecutionOwnership,
    ExecutionOwnershipStore,
    OwnershipClaim,
    RetryBudgetSnapshot,
    _assert_expected_revision,
    _assert_owner,
    _heartbeat,
    _timestamp,
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

    def fail_attempt(
        self, *, failure_code: str, dead_letter: bool = False, **values: Any
    ) -> ExecutionOwnership:
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

    def list_history(
        self, *, tenant_id: str, run_id: str, step_id: str
    ) -> tuple[ExecutionOwnership, ...]:
        with self._read_session() as session:
            rows = session.execute(
                sa.select(WorkflowExecutionAttemptHistoryDB)
                .where(
                    WorkflowExecutionAttemptHistoryDB.tenant_id == str(tenant_id),
                    WorkflowExecutionAttemptHistoryDB.run_id == str(run_id),
                    WorkflowExecutionAttemptHistoryDB.step_id == str(step_id),
                )
                .order_by(WorkflowExecutionAttemptHistoryDB.revision.asc())
            ).scalars().all()
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
