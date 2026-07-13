"""SQLAlchemy implementation of the Hub-owned provider budget store."""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowProviderBudgetDB,
    WorkflowProviderBudgetReservationDB,
)
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.provider_budgets import (
    ProviderBudgetError,
    ProviderBudgetLimits,
    ProviderBudgetSnapshot,
    ProviderBudgetStore,
)
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)


class SQLAlchemyProviderBudgetStore(SQLAlchemyStoreSupport, ProviderBudgetStore):
    """Persistent reservation ledger with row locks, CAS and dedupe IDs."""

    def __init__(self, bind: Engine | SessionFactory) -> None:
        super().__init__(bind)

    def reserve(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        limits: ProviderBudgetLimits,
        reserved_tokens: int,
        reserved_cost_micros: int,
    ) -> ProviderBudgetSnapshot:
        _validate(
            tenant_id=tenant_id,
            run_id=run_id,
            policy_version=policy_version,
            reservation_id=reservation_id,
            limits=limits,
            reserved_tokens=reserved_tokens,
            reserved_cost_micros=reserved_cost_micros,
        )
        for attempt in range(4):
            try:
                return self._reserve_once(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    policy_version=policy_version,
                    reservation_id=reservation_id,
                    limits=limits,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
            except (IntegrityError, OptimisticConcurrencyError):
                if attempt == 3:
                    raise ProviderBudgetError("provider_budget_concurrent_update")
        raise ProviderBudgetError("provider_budget_concurrent_update")

    def _reserve_once(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        limits: ProviderBudgetLimits,
        reserved_tokens: int,
        reserved_cost_micros: int,
    ) -> ProviderBudgetSnapshot:
        budget_id = stable_row_id("wfpb", tenant_id, run_id, policy_version)
        reservation_pk = stable_row_id("wfpbr", tenant_id, run_id, reservation_id)
        with self._transaction() as session:
            reservation = session.get(
                WorkflowProviderBudgetReservationDB,
                reservation_pk,
            )
            statement = sa.select(WorkflowProviderBudgetDB).where(
                WorkflowProviderBudgetDB.id == budget_id
            )
            budget = session.execute(self._for_update(statement)).scalar_one_or_none()
            if reservation is not None:
                if budget is None:
                    raise ProviderBudgetError("provider_budget_aggregate_missing")
                _assert_reservation(
                    reservation,
                    policy_version=policy_version,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
                _assert_limits(budget, limits)
                return _snapshot(budget, reservation)

            if budget is not None:
                _assert_limits(budget, limits)
                _assert_capacity(
                    budget,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
            now = time.time()
            if budget is None:
                budget = WorkflowProviderBudgetDB(
                    id=budget_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    policy_version=policy_version,
                    attempts=1,
                    tokens=reserved_tokens,
                    cost_micros=reserved_cost_micros,
                    maximum_attempts=limits.maximum_attempts,
                    maximum_tokens=limits.maximum_tokens,
                    maximum_cost_micros=limits.maximum_cost_micros,
                    revision=1,
                    updated_at=now,
                )
                session.add(budget)
            else:
                result = session.execute(
                    sa.update(WorkflowProviderBudgetDB)
                    .where(
                        WorkflowProviderBudgetDB.id == budget.id,
                        WorkflowProviderBudgetDB.revision == budget.revision,
                    )
                    .values(
                        attempts=budget.attempts + 1,
                        tokens=budget.tokens + reserved_tokens,
                        cost_micros=budget.cost_micros + reserved_cost_micros,
                        revision=budget.revision + 1,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise OptimisticConcurrencyError(
                        "provider_budget_compare_and_set_failed"
                    )
                budget.attempts += 1
                budget.tokens += reserved_tokens
                budget.cost_micros += reserved_cost_micros
                budget.revision += 1
                budget.updated_at = now
            reservation = WorkflowProviderBudgetReservationDB(
                id=reservation_pk,
                budget_id=budget_id,
                tenant_id=tenant_id,
                run_id=run_id,
                policy_version=policy_version,
                reservation_id=reservation_id,
                reserved_tokens=reserved_tokens,
                reserved_cost_micros=reserved_cost_micros,
                actual_total_tokens=None,
                reconciled=False,
                created_at=now,
                updated_at=now,
            )
            session.add(reservation)
            session.flush()
            return _snapshot(budget, reservation)

    def reconcile(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        actual_total_tokens: int,
    ) -> ProviderBudgetSnapshot:
        if actual_total_tokens < 0:
            raise ProviderBudgetError("provider_budget_actual_tokens_invalid")
        _validate_binding(tenant_id, run_id, policy_version, reservation_id)
        for attempt in range(4):
            try:
                return self._reconcile_once(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    policy_version=policy_version,
                    reservation_id=reservation_id,
                    actual_total_tokens=actual_total_tokens,
                )
            except OptimisticConcurrencyError:
                if attempt == 3:
                    raise ProviderBudgetError("provider_budget_concurrent_update")
        raise ProviderBudgetError("provider_budget_concurrent_update")

    def _reconcile_once(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        actual_total_tokens: int,
    ) -> ProviderBudgetSnapshot:
        budget_id = stable_row_id("wfpb", tenant_id, run_id, policy_version)
        reservation_pk = stable_row_id("wfpbr", tenant_id, run_id, reservation_id)
        with self._transaction() as session:
            reservation_statement = sa.select(
                WorkflowProviderBudgetReservationDB
            ).where(WorkflowProviderBudgetReservationDB.id == reservation_pk)
            reservation = session.execute(
                self._for_update(reservation_statement)
            ).scalar_one_or_none()
            budget_statement = sa.select(WorkflowProviderBudgetDB).where(
                WorkflowProviderBudgetDB.id == budget_id
            )
            budget = session.execute(
                self._for_update(budget_statement)
            ).scalar_one_or_none()
            if reservation is None or budget is None:
                raise ProviderBudgetError("provider_budget_reservation_not_found")
            if (
                reservation.tenant_id != tenant_id
                or reservation.run_id != run_id
                or reservation.policy_version != policy_version
                or reservation.budget_id != budget_id
            ):
                raise ProviderBudgetError(
                    "provider_budget_reservation_binding_mismatch"
                )
            if reservation.reconciled:
                if reservation.actual_total_tokens != actual_total_tokens:
                    raise ProviderBudgetError(
                        "provider_budget_reconciliation_conflict"
                    )
                return _snapshot(budget, reservation)

            now = time.time()
            reconciled_tokens = max(
                0,
                budget.tokens
                + int(actual_total_tokens)
                - int(reservation.reserved_tokens),
            )
            result = session.execute(
                sa.update(WorkflowProviderBudgetDB)
                .where(
                    WorkflowProviderBudgetDB.id == budget.id,
                    WorkflowProviderBudgetDB.revision == budget.revision,
                )
                .values(
                    tokens=reconciled_tokens,
                    revision=budget.revision + 1,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise OptimisticConcurrencyError(
                    "provider_budget_compare_and_set_failed"
                )
            reservation_result = session.execute(
                sa.update(WorkflowProviderBudgetReservationDB)
                .where(
                    WorkflowProviderBudgetReservationDB.id == reservation.id,
                    WorkflowProviderBudgetReservationDB.reconciled.is_(False),
                )
                .values(
                    actual_total_tokens=actual_total_tokens,
                    reconciled=True,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if reservation_result.rowcount != 1:
                raise OptimisticConcurrencyError(
                    "provider_budget_reconciliation_compare_and_set_failed"
                )
            budget.tokens = reconciled_tokens
            budget.revision += 1
            budget.updated_at = now
            reservation.actual_total_tokens = actual_total_tokens
            reservation.reconciled = True
            reservation.updated_at = now
            session.flush()
            return _snapshot(budget, reservation)


def _validate(
    *,
    tenant_id: str,
    run_id: str,
    policy_version: str,
    reservation_id: str,
    limits: ProviderBudgetLimits,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    _validate_binding(tenant_id, run_id, policy_version, reservation_id)
    limits.assert_valid()
    if min(reserved_tokens, reserved_cost_micros) < 0:
        raise ProviderBudgetError("provider_budget_reservation_invalid")


def _validate_binding(*values: str) -> None:
    if any(not value or len(value) > 256 or "\x00" in value for value in values):
        raise ProviderBudgetError("provider_budget_binding_invalid")


def _assert_limits(
    budget: WorkflowProviderBudgetDB,
    limits: ProviderBudgetLimits,
) -> None:
    if (
        budget.maximum_attempts != limits.maximum_attempts
        or budget.maximum_tokens != limits.maximum_tokens
        or budget.maximum_cost_micros != limits.maximum_cost_micros
    ):
        raise ProviderBudgetError("provider_budget_limits_mismatch")


def _assert_capacity(
    budget: WorkflowProviderBudgetDB,
    *,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    if budget.attempts >= budget.maximum_attempts:
        raise ProviderBudgetError("provider_retry_budget_exceeded")
    if budget.maximum_tokens and budget.tokens + reserved_tokens > budget.maximum_tokens:
        raise ProviderBudgetError("provider_token_budget_exceeded")
    if (
        budget.maximum_cost_micros
        and budget.cost_micros + reserved_cost_micros > budget.maximum_cost_micros
    ):
        raise ProviderBudgetError("provider_cost_budget_exceeded")


def _assert_reservation(
    reservation: WorkflowProviderBudgetReservationDB,
    *,
    policy_version: str,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    if (
        reservation.policy_version != policy_version
        or reservation.reserved_tokens != reserved_tokens
        or reservation.reserved_cost_micros != reserved_cost_micros
    ):
        raise ProviderBudgetError("provider_budget_reservation_binding_mismatch")


def _snapshot(
    budget: WorkflowProviderBudgetDB,
    reservation: WorkflowProviderBudgetReservationDB,
) -> ProviderBudgetSnapshot:
    limits = ProviderBudgetLimits(
        maximum_attempts=budget.maximum_attempts,
        maximum_tokens=budget.maximum_tokens,
        maximum_cost_micros=budget.maximum_cost_micros,
    )
    overrun = bool(limits.maximum_tokens and budget.tokens > limits.maximum_tokens)
    return ProviderBudgetSnapshot(
        tenant_id=budget.tenant_id,
        run_id=budget.run_id,
        policy_version=budget.policy_version,
        attempts=budget.attempts,
        tokens=budget.tokens,
        cost_micros=budget.cost_micros,
        limits=limits,
        reservation_id=reservation.reservation_id,
        reserved_tokens=reservation.reserved_tokens,
        reserved_cost_micros=reservation.reserved_cost_micros,
        reconciled=reservation.reconciled,
        reason_code=(
            "provider_budget_overrun_recorded"
            if overrun
            else (
                "provider_budget_reconciled"
                if reservation.reconciled
                else "provider_budget_reserved"
            )
        ),
    )


__all__ = ["SQLAlchemyProviderBudgetStore"]
