"""SQLAlchemy implementation of the Hub-owned provider budget store."""

from __future__ import annotations

import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowProviderBudgetDB,
    WorkflowProviderBudgetReservationDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
)
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.provider_budgets import (
    ProviderBudgetError,
    ProviderBudgetLimits,
    ProviderBudgetSnapshot,
    ProviderBudgetStore,
    ProviderProfileAttemptReservation,
    ProviderScopedBudgetReservation,
)
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)


@dataclass(frozen=True)
class _ProfileAttemptState:
    budgets_by_scope: dict[str, WorkflowRetryBudgetDB]
    current_consumption: WorkflowRetryConsumptionDB | None


@dataclass(frozen=True)
class _BudgetScope:
    scope_id: str
    limits: ProviderBudgetLimits | None
    budget_id: str
    reservation_pk: str


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
        profile_attempt: ProviderProfileAttemptReservation | None = None,
        scoped_budget: ProviderScopedBudgetReservation | None = None,
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
        if profile_attempt is not None:
            profile_attempt.assert_valid()
        _validate_scoped_budget(scoped_budget, run_id=run_id)
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
                    profile_attempt=profile_attempt,
                    scoped_budget=scoped_budget,
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
        profile_attempt: ProviderProfileAttemptReservation | None,
        scoped_budget: ProviderScopedBudgetReservation | None,
    ) -> ProviderBudgetSnapshot:
        scopes = _budget_scopes(
            tenant_id=tenant_id,
            run_id=run_id,
            policy_version=policy_version,
            reservation_id=reservation_id,
            limits=limits,
            scoped_budget=scoped_budget,
        )
        with self._transaction() as session:
            reservation_rows = session.execute(
                self._for_update(
                    sa.select(WorkflowProviderBudgetReservationDB)
                    .where(
                        WorkflowProviderBudgetReservationDB.id.in_(
                            [scope.reservation_pk for scope in scopes]
                        )
                    )
                    .order_by(WorkflowProviderBudgetReservationDB.id)
                )
            ).scalars().all()
            budget_rows = session.execute(
                self._for_update(
                    sa.select(WorkflowProviderBudgetDB)
                    .where(
                        WorkflowProviderBudgetDB.id.in_(
                            [scope.budget_id for scope in scopes]
                        )
                    )
                    .order_by(WorkflowProviderBudgetDB.id)
                )
            ).scalars().all()
            reservations_by_scope = {
                row.run_id: row for row in reservation_rows
            }
            budgets_by_scope = {
                row.run_id: row for row in budget_rows
            }
            profile_state = _load_profile_attempt_state(
                session,
                tenant_id=tenant_id,
                profile_attempt=profile_attempt,
                lock=self._for_update,
            )
            if reservations_by_scope:
                if set(reservations_by_scope) != {
                    scope.scope_id for scope in scopes
                }:
                    if (
                        scoped_budget is not None
                        and run_id in reservations_by_scope
                        and scoped_budget.scope_id
                        not in reservations_by_scope
                    ):
                        raise ProviderBudgetError(
                            "provider_scoped_budget_migration_required"
                        )
                    raise ProviderBudgetError(
                        "provider_budget_atomic_binding_mismatch"
                    )
                for scope in scopes:
                    budget = budgets_by_scope.get(scope.scope_id)
                    reservation = reservations_by_scope[scope.scope_id]
                    if budget is None:
                        raise ProviderBudgetError(
                            "provider_budget_aggregate_missing"
                        )
                    _assert_reservation_scope_binding(
                        reservation,
                        budget=budget,
                        tenant_id=tenant_id,
                        scope=scope,
                        policy_version=policy_version,
                        reserved_tokens=reserved_tokens,
                        reserved_cost_micros=reserved_cost_micros,
                    )
                    if scope.limits is not None:
                        _assert_limits(budget, scope.limits)
                profile_attempts, profile_maximum = (
                    _assert_profile_attempt_replay(
                        profile_state,
                        profile_attempt=profile_attempt,
                    )
                )
                return _snapshot(
                    budgets_by_scope[run_id],
                    reservations_by_scope[run_id],
                    profile_attempts=profile_attempts,
                    profile_maximum_attempts=profile_maximum,
                    scoped_budget=budgets_by_scope.get(
                        scoped_budget.scope_id
                    )
                    if scoped_budget is not None
                    else None,
                )

            for scope in scopes:
                budget = budgets_by_scope.get(scope.scope_id)
                if scope.limits is None:
                    raise ProviderBudgetError(
                        "provider_budget_limits_invalid"
                    )
                if budget is not None:
                    _assert_limits(budget, scope.limits)
                    _assert_capacity(
                        budget,
                        reserved_tokens=reserved_tokens,
                        reserved_cost_micros=reserved_cost_micros,
                    )
                else:
                    _assert_initial_capacity(
                        scope.limits,
                        reserved_tokens=reserved_tokens,
                        reserved_cost_micros=reserved_cost_micros,
                    )
            _assert_profile_attempt_capacity(
                profile_state,
                profile_attempt=profile_attempt,
            )
            now = time.time()
            for scope in scopes:
                budget = budgets_by_scope.get(scope.scope_id)
                if budget is None:
                    budget = WorkflowProviderBudgetDB(
                        id=scope.budget_id,
                        tenant_id=tenant_id,
                        run_id=scope.scope_id,
                        policy_version=policy_version,
                        attempts=1,
                        tokens=reserved_tokens,
                        cost_micros=reserved_cost_micros,
                        maximum_attempts=scope.limits.maximum_attempts,
                        maximum_tokens=scope.limits.maximum_tokens,
                        maximum_cost_micros=(
                            scope.limits.maximum_cost_micros
                        ),
                        revision=1,
                        updated_at=now,
                    )
                    session.add(budget)
                    budgets_by_scope[scope.scope_id] = budget
                else:
                    _increment_budget(
                        session,
                        budget=budget,
                        reserved_tokens=reserved_tokens,
                        reserved_cost_micros=reserved_cost_micros,
                        now=now,
                    )
                reservation = WorkflowProviderBudgetReservationDB(
                    id=scope.reservation_pk,
                    budget_id=scope.budget_id,
                    tenant_id=tenant_id,
                    run_id=scope.scope_id,
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
                reservations_by_scope[scope.scope_id] = reservation
            profile_attempts, profile_maximum = _consume_profile_attempt(
                session,
                tenant_id=tenant_id,
                profile_attempt=profile_attempt,
                state=profile_state,
                now=now,
            )
            session.flush()
            return _snapshot(
                budgets_by_scope[run_id],
                reservations_by_scope[run_id],
                profile_attempts=profile_attempts,
                profile_maximum_attempts=profile_maximum,
                scoped_budget=budgets_by_scope.get(
                    scoped_budget.scope_id
                )
                if scoped_budget is not None
                else None,
            )

    def reconcile(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        actual_total_tokens: int,
        profile_attempt: ProviderProfileAttemptReservation | None = None,
        scoped_budget: ProviderScopedBudgetReservation | None = None,
    ) -> ProviderBudgetSnapshot:
        if actual_total_tokens < 0:
            raise ProviderBudgetError("provider_budget_actual_tokens_invalid")
        _validate_binding(tenant_id, run_id, policy_version, reservation_id)
        if profile_attempt is not None:
            profile_attempt.assert_valid()
        _validate_scoped_budget(scoped_budget, run_id=run_id)
        for attempt in range(4):
            try:
                return self._reconcile_once(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    policy_version=policy_version,
                    reservation_id=reservation_id,
                    actual_total_tokens=actual_total_tokens,
                    profile_attempt=profile_attempt,
                    scoped_budget=scoped_budget,
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
        profile_attempt: ProviderProfileAttemptReservation | None,
        scoped_budget: ProviderScopedBudgetReservation | None,
    ) -> ProviderBudgetSnapshot:
        scopes = _budget_scopes(
            tenant_id=tenant_id,
            run_id=run_id,
            policy_version=policy_version,
            reservation_id=reservation_id,
            limits=None,
            scoped_budget=scoped_budget,
        )
        with self._transaction() as session:
            reservations = session.execute(
                self._for_update(
                    sa.select(WorkflowProviderBudgetReservationDB)
                    .where(
                        WorkflowProviderBudgetReservationDB.id.in_(
                            [scope.reservation_pk for scope in scopes]
                        )
                    )
                    .order_by(WorkflowProviderBudgetReservationDB.id)
                )
            ).scalars().all()
            budgets = session.execute(
                self._for_update(
                    sa.select(WorkflowProviderBudgetDB)
                    .where(
                        WorkflowProviderBudgetDB.id.in_(
                            [scope.budget_id for scope in scopes]
                        )
                    )
                    .order_by(WorkflowProviderBudgetDB.id)
                )
            ).scalars().all()
            reservations_by_scope = {
                row.run_id: row for row in reservations
            }
            budgets_by_scope = {row.run_id: row for row in budgets}
            profile_state = _load_profile_attempt_state(
                session,
                tenant_id=tenant_id,
                profile_attempt=profile_attempt,
                lock=self._for_update,
            )
            if (
                set(reservations_by_scope)
                != {scope.scope_id for scope in scopes}
                or set(budgets_by_scope)
                != {scope.scope_id for scope in scopes}
            ):
                if (
                    scoped_budget is not None
                    and run_id in reservations_by_scope
                    and scoped_budget.scope_id
                    not in reservations_by_scope
                ):
                    raise ProviderBudgetError(
                        "provider_scoped_budget_migration_required"
                    )
                raise ProviderBudgetError("provider_budget_reservation_not_found")
            aggregate_reservation = reservations_by_scope[run_id]
            for scope in scopes:
                budget = budgets_by_scope[scope.scope_id]
                reservation = reservations_by_scope[scope.scope_id]
                _assert_reservation_scope_binding(
                    reservation,
                    budget=budget,
                    tenant_id=tenant_id,
                    scope=scope,
                    policy_version=policy_version,
                    reserved_tokens=aggregate_reservation.reserved_tokens,
                    reserved_cost_micros=(
                        aggregate_reservation.reserved_cost_micros
                    ),
                )
                if scope.limits is not None:
                    _assert_limits(budget, scope.limits)
            reconciled = {
                bool(reservations_by_scope[scope.scope_id].reconciled)
                for scope in scopes
            }
            if True in reconciled:
                if reconciled != {True} or any(
                    reservations_by_scope[
                        scope.scope_id
                    ].actual_total_tokens
                    != actual_total_tokens
                    for scope in scopes
                ):
                    raise ProviderBudgetError(
                        "provider_budget_reconciliation_conflict"
                    )
                profile_attempts, profile_maximum = (
                    _assert_profile_attempt_replay(
                        profile_state,
                        profile_attempt=profile_attempt,
                    )
                )
                return _snapshot(
                    budgets_by_scope[run_id],
                    reservations_by_scope[run_id],
                    profile_attempts=profile_attempts,
                    profile_maximum_attempts=profile_maximum,
                    scoped_budget=budgets_by_scope.get(
                        scoped_budget.scope_id
                    )
                    if scoped_budget is not None
                    else None,
                )

            now = time.time()
            for scope in scopes:
                _reconcile_scope(
                    session,
                    budget=budgets_by_scope[scope.scope_id],
                    reservation=reservations_by_scope[scope.scope_id],
                    actual_total_tokens=actual_total_tokens,
                    now=now,
                )
            session.flush()
            profile_attempts, profile_maximum = (
                _assert_profile_attempt_replay(
                    profile_state,
                    profile_attempt=profile_attempt,
                )
            )
            return _snapshot(
                budgets_by_scope[run_id],
                reservations_by_scope[run_id],
                profile_attempts=profile_attempts,
                profile_maximum_attempts=profile_maximum,
                scoped_budget=budgets_by_scope.get(
                    scoped_budget.scope_id
                )
                if scoped_budget is not None
                else None,
            )


def _validate_scoped_budget(
    scoped_budget: ProviderScopedBudgetReservation | None,
    *,
    run_id: str,
) -> None:
    if scoped_budget is None:
        return
    scoped_budget.assert_valid()
    if scoped_budget.scope_id == run_id:
        raise ProviderBudgetError("provider_scoped_budget_invalid")


def _budget_scopes(
    *,
    tenant_id: str,
    run_id: str,
    policy_version: str,
    reservation_id: str,
    limits: ProviderBudgetLimits | None,
    scoped_budget: ProviderScopedBudgetReservation | None,
) -> tuple[_BudgetScope, ...]:
    values = [(run_id, limits)]
    if scoped_budget is not None:
        values.append((scoped_budget.scope_id, scoped_budget.limits))
    scopes = [
        _BudgetScope(
            scope_id=scope_id,
            limits=scope_limits,
            budget_id=stable_row_id(
                "wfpb",
                tenant_id,
                scope_id,
                policy_version,
            ),
            reservation_pk=stable_row_id(
                "wfpbr",
                tenant_id,
                scope_id,
                reservation_id,
            ),
        )
        for scope_id, scope_limits in values
    ]
    return tuple(sorted(scopes, key=lambda item: item.budget_id))


def _assert_reservation_scope_binding(
    reservation: WorkflowProviderBudgetReservationDB,
    *,
    budget: WorkflowProviderBudgetDB,
    tenant_id: str,
    scope: _BudgetScope,
    policy_version: str,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    if (
        budget.id != scope.budget_id
        or budget.tenant_id != tenant_id
        or budget.run_id != scope.scope_id
        or budget.policy_version != policy_version
        or reservation.budget_id != scope.budget_id
        or reservation.tenant_id != tenant_id
        or reservation.run_id != scope.scope_id
        or reservation.policy_version != policy_version
    ):
        raise ProviderBudgetError(
            "provider_budget_reservation_binding_mismatch"
        )
    _assert_reservation(
        reservation,
        policy_version=policy_version,
        reserved_tokens=reserved_tokens,
        reserved_cost_micros=reserved_cost_micros,
    )


def _increment_budget(
    session,
    *,
    budget: WorkflowProviderBudgetDB,
    reserved_tokens: int,
    reserved_cost_micros: int,
    now: float,
) -> None:
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


def _reconcile_scope(
    session,
    *,
    budget: WorkflowProviderBudgetDB,
    reservation: WorkflowProviderBudgetReservationDB,
    actual_total_tokens: int,
    now: float,
) -> None:
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
    if (
        budget.maximum_attempts
        and budget.attempts >= budget.maximum_attempts
    ):
        raise ProviderBudgetError("provider_retry_budget_exceeded")
    if budget.maximum_tokens and budget.tokens + reserved_tokens > budget.maximum_tokens:
        raise ProviderBudgetError("provider_token_budget_exceeded")
    if (
        budget.maximum_cost_micros
        and budget.cost_micros + reserved_cost_micros > budget.maximum_cost_micros
    ):
        raise ProviderBudgetError("provider_cost_budget_exceeded")


def _assert_initial_capacity(
    limits: ProviderBudgetLimits,
    *,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    if limits.maximum_attempts and limits.maximum_attempts < 1:
        raise ProviderBudgetError("provider_retry_budget_exceeded")
    if limits.maximum_tokens and reserved_tokens > limits.maximum_tokens:
        raise ProviderBudgetError("provider_token_budget_exceeded")
    if (
        limits.maximum_cost_micros
        and reserved_cost_micros > limits.maximum_cost_micros
    ):
        raise ProviderBudgetError("provider_cost_budget_exceeded")


def _load_profile_attempt_state(
    session,
    *,
    tenant_id: str,
    profile_attempt: ProviderProfileAttemptReservation | None,
    lock,
) -> _ProfileAttemptState | None:
    if profile_attempt is None:
        return None
    scopes = (
        *profile_attempt.predecessors,
        profile_attempt.current,
    )
    budget_ids = tuple(
        sorted(
            stable_row_id("wfrb", tenant_id, scope.scope_id)
            for scope in scopes
        )
    )
    statement = (
        sa.select(WorkflowRetryBudgetDB)
        .where(WorkflowRetryBudgetDB.id.in_(budget_ids))
        .order_by(WorkflowRetryBudgetDB.id)
    )
    rows = session.execute(lock(statement)).scalars().all()
    consumption_id = stable_row_id(
        "wfrr",
        tenant_id,
        profile_attempt.current.scope_id,
        profile_attempt.reservation_id,
    )
    consumption_statement = sa.select(
        WorkflowRetryConsumptionDB
    ).where(WorkflowRetryConsumptionDB.id == consumption_id)
    consumption = session.execute(
        lock(consumption_statement)
    ).scalar_one_or_none()
    return _ProfileAttemptState(
        budgets_by_scope={row.run_id: row for row in rows},
        current_consumption=consumption,
    )


def _assert_profile_attempt_capacity(
    state: _ProfileAttemptState | None,
    *,
    profile_attempt: ProviderProfileAttemptReservation | None,
) -> None:
    if profile_attempt is None:
        return
    if state is None:
        raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
    for predecessor in profile_attempt.predecessors:
        row = state.budgets_by_scope.get(predecessor.scope_id)
        if row is not None and row.maximum != predecessor.maximum_attempts:
            raise ProviderBudgetError("retry_budget_maximum_mismatch")
        used = int(row.used if row is not None else 0)
        if used != predecessor.maximum_attempts:
            raise ProviderBudgetError(
                "provider_attempt_plan_sequence_denied"
            )
    current = state.budgets_by_scope.get(
        profile_attempt.current.scope_id
    )
    if (
        current is not None
        and current.maximum != profile_attempt.current.maximum_attempts
    ):
        raise ProviderBudgetError("retry_budget_maximum_mismatch")
    if state.current_consumption is not None:
        raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
    if (
        current is not None
        and current.used >= profile_attempt.current.maximum_attempts
    ):
        raise ProviderBudgetError("provider_retry_budget_exceeded")


def _assert_profile_attempt_replay(
    state: _ProfileAttemptState | None,
    *,
    profile_attempt: ProviderProfileAttemptReservation | None,
) -> tuple[int | None, int | None]:
    if profile_attempt is None:
        return None, None
    if state is None:
        raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
    current = state.budgets_by_scope.get(
        profile_attempt.current.scope_id
    )
    consumption = state.current_consumption
    if (
        current is None
        or current.maximum != profile_attempt.current.maximum_attempts
        or consumption is None
        or consumption.tenant_id != current.tenant_id
        or consumption.run_id != current.run_id
        or consumption.retry_id != profile_attempt.reservation_id
        or consumption.category != "provider"
    ):
        raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
    return int(current.used), int(current.maximum)


def _consume_profile_attempt(
    session,
    *,
    tenant_id: str,
    profile_attempt: ProviderProfileAttemptReservation | None,
    state: _ProfileAttemptState | None,
    now: float,
) -> tuple[int | None, int | None]:
    if profile_attempt is None:
        return None, None
    if state is None:
        raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
    scope = profile_attempt.current
    consumption_id = stable_row_id(
        "wfrr",
        tenant_id,
        scope.scope_id,
        profile_attempt.reservation_id,
    )
    session.add(
        WorkflowRetryConsumptionDB(
            id=consumption_id,
            tenant_id=tenant_id,
            run_id=scope.scope_id,
            retry_id=profile_attempt.reservation_id,
            category="provider",
            consumed_at=now,
        )
    )
    budget = state.budgets_by_scope.get(scope.scope_id)
    if budget is None:
        session.add(
            WorkflowRetryBudgetDB(
                id=stable_row_id("wfrb", tenant_id, scope.scope_id),
                tenant_id=tenant_id,
                run_id=scope.scope_id,
                used=1,
                maximum=scope.maximum_attempts,
                revision=1,
                updated_at=now,
            )
        )
        return 1, scope.maximum_attempts
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
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise OptimisticConcurrencyError(
            "provider_attempt_budget_compare_and_set_failed"
        )
    budget.used += 1
    budget.revision += 1
    budget.updated_at = now
    return int(budget.used), int(budget.maximum)


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
    *,
    profile_attempts: int | None = None,
    profile_maximum_attempts: int | None = None,
    scoped_budget: WorkflowProviderBudgetDB | None = None,
) -> ProviderBudgetSnapshot:
    limits = ProviderBudgetLimits(
        maximum_attempts=budget.maximum_attempts,
        maximum_tokens=budget.maximum_tokens,
        maximum_cost_micros=budget.maximum_cost_micros,
    )
    overrun = bool(limits.maximum_tokens and budget.tokens > limits.maximum_tokens)
    scoped_overrun = bool(
        scoped_budget is not None
        and (
            (
                scoped_budget.maximum_tokens
                and scoped_budget.tokens > scoped_budget.maximum_tokens
            )
            or (
                scoped_budget.maximum_cost_micros
                and scoped_budget.cost_micros
                > scoped_budget.maximum_cost_micros
            )
        )
    )
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
            "provider_scoped_budget_overrun_recorded"
            if scoped_overrun
            else "provider_budget_overrun_recorded"
            if overrun
            else (
                "provider_budget_reconciled"
                if reservation.reconciled
                else "provider_budget_reserved"
            )
        ),
        profile_attempts=profile_attempts,
        profile_maximum_attempts=profile_maximum_attempts,
        scoped_tokens=(
            scoped_budget.tokens if scoped_budget is not None else None
        ),
        scoped_cost_micros=(
            scoped_budget.cost_micros
            if scoped_budget is not None
            else None
        ),
        scoped_budget_overrun=scoped_overrun,
    )


__all__ = ["SQLAlchemyProviderBudgetStore"]
