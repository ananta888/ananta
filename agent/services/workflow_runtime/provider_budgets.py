"""Hub-owned aggregate provider budgets and idempotent call reservations.

Provider processes only request reservations.  The Hub owns the shared counter,
so two workers cannot each spend the full token/cost budget independently.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

PROVIDER_BUDGET_RECEIPT_SCHEMA = "ananta.provider-budget-receipt.v1"


class ProviderBudgetError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "provider_budget_denied")
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class ProviderBudgetLimits:
    maximum_attempts: int
    maximum_tokens: int
    maximum_cost_micros: int

    def assert_valid(self) -> None:
        if self.maximum_attempts < 0 or min(
            self.maximum_tokens,
            self.maximum_cost_micros,
        ) < 0:
            raise ProviderBudgetError("provider_budget_limits_invalid")


@dataclass(frozen=True)
class ProviderAttemptScope:
    scope_id: str
    maximum_attempts: int

    def assert_valid(self) -> None:
        if (
            not self.scope_id
            or len(self.scope_id) > 256
            or "\x00" in self.scope_id
            or self.maximum_attempts < 1
        ):
            raise ProviderBudgetError("provider_attempt_scope_invalid")


@dataclass(frozen=True)
class ProviderProfileAttemptReservation:
    """Profile attempt mutation committed with the aggregate reservation."""

    current: ProviderAttemptScope
    reservation_id: str
    predecessors: tuple[ProviderAttemptScope, ...] = ()

    def assert_valid(self) -> None:
        self.current.assert_valid()
        if (
            not self.reservation_id
            or len(self.reservation_id) > 256
            or "\x00" in self.reservation_id
        ):
            raise ProviderBudgetError("provider_attempt_reservation_invalid")
        seen: set[str] = {self.current.scope_id}
        for predecessor in self.predecessors:
            predecessor.assert_valid()
            if predecessor.scope_id in seen:
                raise ProviderBudgetError("provider_attempt_scope_duplicate")
            seen.add(predecessor.scope_id)


@dataclass(frozen=True)
class ProviderScopedBudgetReservation:
    """A second aggregate enforced atomically with the run-wide budget.

    The Hub uses this for one signed workflow node/attempt.  Reusing the same
    provider budget tables keeps node and run reservations in one transaction
    without introducing another persistence subsystem.
    """

    scope_id: str
    limits: ProviderBudgetLimits

    def assert_valid(self) -> None:
        if (
            not self.scope_id
            or len(self.scope_id) > 256
            or "\x00" in self.scope_id
        ):
            raise ProviderBudgetError("provider_scoped_budget_invalid")
        self.limits.assert_valid()


@dataclass(frozen=True)
class ProviderBudgetSnapshot:
    tenant_id: str
    run_id: str
    policy_version: str
    attempts: int
    tokens: int
    cost_micros: int
    limits: ProviderBudgetLimits
    reservation_id: str
    reserved_tokens: int
    reserved_cost_micros: int
    reconciled: bool = False
    reason_code: str = "provider_budget_reserved"
    profile_attempts: int | None = None
    profile_maximum_attempts: int | None = None
    scoped_tokens: int | None = None
    scoped_cost_micros: int | None = None
    scoped_budget_overrun: bool = False
    schema: str = PROVIDER_BUDGET_RECEIPT_SCHEMA

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "policy_version": self.policy_version,
            "reservation_id": self.reservation_id,
            "attempts": self.attempts,
            "tokens": self.tokens,
            "cost_micros": self.cost_micros,
            "reserved_tokens": self.reserved_tokens,
            "reserved_cost_micros": self.reserved_cost_micros,
            "maximum_attempts": self.limits.maximum_attempts,
            "maximum_tokens": self.limits.maximum_tokens,
            "maximum_cost_micros": self.limits.maximum_cost_micros,
            "remaining_attempts": max(0, self.limits.maximum_attempts - self.attempts),
            "remaining_tokens": (
                max(0, self.limits.maximum_tokens - self.tokens)
                if self.limits.maximum_tokens
                else None
            ),
            "remaining_cost_micros": (
                max(0, self.limits.maximum_cost_micros - self.cost_micros)
                if self.limits.maximum_cost_micros
                else None
            ),
            "reconciled": self.reconciled,
            "reason_code": self.reason_code,
        }
        if self.profile_attempts is not None:
            payload["profile_attempts"] = self.profile_attempts
            payload["profile_maximum_attempts"] = (
                self.profile_maximum_attempts
            )
        if self.scoped_tokens is not None:
            payload.update(
                {
                    "scoped_tokens": self.scoped_tokens,
                    "scoped_cost_micros": self.scoped_cost_micros,
                    "scoped_budget_overrun": (
                        self.scoped_budget_overrun
                    ),
                }
            )
        return payload


class ProviderBudgetStore(Protocol):
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
    ) -> ProviderBudgetSnapshot: ...

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
    ) -> ProviderBudgetSnapshot: ...


@dataclass
class _Usage:
    limits: ProviderBudgetLimits
    attempts: int = 0
    tokens: int = 0
    cost_micros: int = 0


@dataclass
class _Reservation:
    policy_version: str
    reserved_tokens: int
    reserved_cost_micros: int
    actual_total_tokens: int | None = None


@dataclass
class _ProfileAttemptUsage:
    maximum: int
    used: int = 0


class InMemoryProviderBudgetStore:
    """Thread-safe contract adapter used by deterministic tests."""

    def __init__(self) -> None:
        self._usage: dict[tuple[str, str, str], _Usage] = {}
        self._reservations: dict[tuple[str, str, str], _Reservation] = {}
        self._profile_attempt_usage: dict[
            tuple[str, str], _ProfileAttemptUsage
        ] = {}
        self._profile_attempt_reservations: set[
            tuple[str, str, str]
        ] = set()
        self._lock = threading.RLock()

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
        _validate_binding(tenant_id, run_id, policy_version, reservation_id)
        _validate_reservation(limits, reserved_tokens, reserved_cost_micros)
        if profile_attempt is not None:
            profile_attempt.assert_valid()
        if scoped_budget is not None:
            scoped_budget.assert_valid()
            if scoped_budget.scope_id == run_id:
                raise ProviderBudgetError("provider_scoped_budget_invalid")
        usage_key = (tenant_id, run_id, policy_version)
        reservation_key = (tenant_id, run_id, reservation_id)
        scoped_usage_key = (
            (tenant_id, scoped_budget.scope_id, policy_version)
            if scoped_budget is not None
            else None
        )
        scoped_reservation_key = (
            (tenant_id, scoped_budget.scope_id, reservation_id)
            if scoped_budget is not None
            else None
        )
        with self._lock:
            usage = self._usage.get(usage_key)
            if usage is not None and usage.limits != limits:
                raise ProviderBudgetError("provider_budget_limits_mismatch")
            scoped_usage = (
                self._usage.get(scoped_usage_key)
                if scoped_usage_key is not None
                else None
            )
            if (
                scoped_usage is not None
                and scoped_budget is not None
                and scoped_usage.limits != scoped_budget.limits
            ):
                raise ProviderBudgetError("provider_budget_limits_mismatch")
            existing = self._reservations.get(reservation_key)
            scoped_existing = (
                self._reservations.get(scoped_reservation_key)
                if scoped_reservation_key is not None
                else None
            )
            if existing is not None:
                if usage is None:
                    raise ProviderBudgetError(
                        "provider_budget_aggregate_missing"
                    )
                _assert_reservation_binding(
                    existing,
                    policy_version=policy_version,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
                _assert_scoped_budget_replay(
                    scoped_budget=scoped_budget,
                    usage=scoped_usage,
                    reservation=scoped_existing,
                    policy_version=policy_version,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
                profile_usage = self._profile_attempt_for_replay(
                    tenant_id=tenant_id,
                    profile_attempt=profile_attempt,
                )
                return _snapshot(
                    tenant_id,
                    run_id,
                    policy_version,
                    reservation_id,
                    usage,
                    existing,
                    profile_usage=profile_usage,
                    scoped_usage=scoped_usage,
                )
            if scoped_existing is not None:
                raise ProviderBudgetError(
                    "provider_budget_atomic_binding_mismatch"
                )
            profile_usage = self._assert_profile_attempt_capacity(
                tenant_id=tenant_id,
                profile_attempt=profile_attempt,
            )
            candidate_usage = usage or _Usage(limits=limits)
            _assert_capacity(
                candidate_usage,
                reserved_tokens,
                reserved_cost_micros,
            )
            candidate_scoped_usage = (
                scoped_usage
                or (
                    _Usage(limits=scoped_budget.limits)
                    if scoped_budget is not None
                    else None
                )
            )
            if candidate_scoped_usage is not None:
                _assert_capacity(
                    candidate_scoped_usage,
                    reserved_tokens,
                    reserved_cost_micros,
                )
            if usage is None:
                usage = candidate_usage
                self._usage[usage_key] = usage
            if (
                scoped_usage is None
                and scoped_usage_key is not None
                and candidate_scoped_usage is not None
            ):
                scoped_usage = candidate_scoped_usage
                self._usage[scoped_usage_key] = scoped_usage
            reservation = _Reservation(
                policy_version=policy_version,
                reserved_tokens=reserved_tokens,
                reserved_cost_micros=reserved_cost_micros,
            )
            self._reservations[reservation_key] = reservation
            usage.attempts += 1
            usage.tokens += reserved_tokens
            usage.cost_micros += reserved_cost_micros
            if (
                scoped_usage is not None
                and scoped_reservation_key is not None
            ):
                scoped_usage.attempts += 1
                scoped_usage.tokens += reserved_tokens
                scoped_usage.cost_micros += reserved_cost_micros
                self._reservations[scoped_reservation_key] = _Reservation(
                    policy_version=policy_version,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
            if profile_attempt is not None:
                if profile_usage is None:
                    profile_usage = _ProfileAttemptUsage(
                        maximum=profile_attempt.current.maximum_attempts,
                    )
                    self._profile_attempt_usage[
                        (tenant_id, profile_attempt.current.scope_id)
                    ] = profile_usage
                profile_usage.used += 1
                self._profile_attempt_reservations.add(
                    (
                        tenant_id,
                        profile_attempt.current.scope_id,
                        profile_attempt.reservation_id,
                    )
                )
            return _snapshot(
                tenant_id,
                run_id,
                policy_version,
                reservation_id,
                usage,
                reservation,
                profile_usage=profile_usage,
                scoped_usage=scoped_usage,
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
        _validate_binding(tenant_id, run_id, policy_version, reservation_id)
        if actual_total_tokens < 0:
            raise ProviderBudgetError("provider_budget_actual_tokens_invalid")
        if profile_attempt is not None:
            profile_attempt.assert_valid()
        if scoped_budget is not None:
            scoped_budget.assert_valid()
            if scoped_budget.scope_id == run_id:
                raise ProviderBudgetError("provider_scoped_budget_invalid")
        with self._lock:
            usage = self._usage.get((tenant_id, run_id, policy_version))
            reservation = self._reservations.get((tenant_id, run_id, reservation_id))
            if usage is None or reservation is None:
                raise ProviderBudgetError("provider_budget_reservation_not_found")
            if reservation.policy_version != policy_version:
                raise ProviderBudgetError("provider_budget_reservation_binding_mismatch")
            scoped_usage = (
                self._usage.get(
                    (tenant_id, scoped_budget.scope_id, policy_version)
                )
                if scoped_budget is not None
                else None
            )
            scoped_reservation = (
                self._reservations.get(
                    (tenant_id, scoped_budget.scope_id, reservation_id)
                )
                if scoped_budget is not None
                else None
            )
            _assert_scoped_budget_replay(
                scoped_budget=scoped_budget,
                usage=scoped_usage,
                reservation=scoped_reservation,
                policy_version=policy_version,
                reserved_tokens=reservation.reserved_tokens,
                reserved_cost_micros=reservation.reserved_cost_micros,
            )
            profile_usage = self._profile_attempt_for_replay(
                tenant_id=tenant_id,
                profile_attempt=profile_attempt,
            )
            if reservation.actual_total_tokens is not None:
                if reservation.actual_total_tokens != actual_total_tokens:
                    raise ProviderBudgetError("provider_budget_reconciliation_conflict")
                if (
                    scoped_reservation is not None
                    and scoped_reservation.actual_total_tokens
                    != actual_total_tokens
                ):
                    raise ProviderBudgetError(
                        "provider_budget_reconciliation_conflict"
                    )
                return _snapshot(
                    tenant_id,
                    run_id,
                    policy_version,
                    reservation_id,
                    usage,
                    reservation,
                    profile_usage=profile_usage,
                    scoped_usage=scoped_usage,
                )
            usage.tokens = max(
                0,
                usage.tokens + actual_total_tokens - reservation.reserved_tokens,
            )
            reservation.actual_total_tokens = actual_total_tokens
            if (
                scoped_usage is not None
                and scoped_reservation is not None
            ):
                if scoped_reservation.actual_total_tokens is not None:
                    raise ProviderBudgetError(
                        "provider_budget_atomic_binding_mismatch"
                    )
                scoped_usage.tokens = max(
                    0,
                    scoped_usage.tokens
                    + actual_total_tokens
                    - scoped_reservation.reserved_tokens,
                )
                scoped_reservation.actual_total_tokens = (
                    actual_total_tokens
                )
            return _snapshot(
                tenant_id,
                run_id,
                policy_version,
                reservation_id,
                usage,
                reservation,
                profile_usage=profile_usage,
                scoped_usage=scoped_usage,
            )

    def _assert_profile_attempt_capacity(
        self,
        *,
        tenant_id: str,
        profile_attempt: ProviderProfileAttemptReservation | None,
    ) -> _ProfileAttemptUsage | None:
        if profile_attempt is None:
            return None
        for predecessor in profile_attempt.predecessors:
            usage = self._profile_attempt_usage.get(
                (tenant_id, predecessor.scope_id)
            )
            used = int(usage.used if usage is not None else 0)
            if usage is not None and usage.maximum != predecessor.maximum_attempts:
                raise ProviderBudgetError("retry_budget_maximum_mismatch")
            if used != predecessor.maximum_attempts:
                raise ProviderBudgetError(
                    "provider_attempt_plan_sequence_denied"
                )
        key = (tenant_id, profile_attempt.current.scope_id)
        usage = self._profile_attempt_usage.get(key)
        if (
            usage is not None
            and usage.maximum != profile_attempt.current.maximum_attempts
        ):
            raise ProviderBudgetError("retry_budget_maximum_mismatch")
        if (
            tenant_id,
            profile_attempt.current.scope_id,
            profile_attempt.reservation_id,
        ) in self._profile_attempt_reservations:
            raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
        if (
            usage is not None
            and usage.used >= profile_attempt.current.maximum_attempts
        ):
            raise ProviderBudgetError("provider_retry_budget_exceeded")
        return usage

    def _profile_attempt_for_replay(
        self,
        *,
        tenant_id: str,
        profile_attempt: ProviderProfileAttemptReservation | None,
    ) -> _ProfileAttemptUsage | None:
        if profile_attempt is None:
            return None
        key = (tenant_id, profile_attempt.current.scope_id)
        usage = self._profile_attempt_usage.get(key)
        if (
            usage is None
            or usage.maximum != profile_attempt.current.maximum_attempts
            or (
                tenant_id,
                profile_attempt.current.scope_id,
                profile_attempt.reservation_id,
            )
            not in self._profile_attempt_reservations
        ):
            raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
        return usage


def _validate_binding(
    tenant_id: str,
    run_id: str,
    policy_version: str,
    reservation_id: str,
) -> None:
    values = (tenant_id, run_id, policy_version, reservation_id)
    if any(not value or len(value) > 256 or "\x00" in value for value in values):
        raise ProviderBudgetError("provider_budget_binding_invalid")


def _validate_reservation(
    limits: ProviderBudgetLimits,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    limits.assert_valid()
    if min(reserved_tokens, reserved_cost_micros) < 0:
        raise ProviderBudgetError("provider_budget_reservation_invalid")


def _assert_capacity(
    usage: _Usage,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    if (
        usage.limits.maximum_attempts
        and usage.attempts >= usage.limits.maximum_attempts
    ):
        raise ProviderBudgetError("provider_retry_budget_exceeded")
    if usage.limits.maximum_tokens and usage.tokens + reserved_tokens > usage.limits.maximum_tokens:
        raise ProviderBudgetError("provider_token_budget_exceeded")
    if (
        usage.limits.maximum_cost_micros
        and usage.cost_micros + reserved_cost_micros > usage.limits.maximum_cost_micros
    ):
        raise ProviderBudgetError("provider_cost_budget_exceeded")


def _assert_reservation_binding(
    reservation: _Reservation,
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


def _assert_scoped_budget_replay(
    *,
    scoped_budget: ProviderScopedBudgetReservation | None,
    usage: _Usage | None,
    reservation: _Reservation | None,
    policy_version: str,
    reserved_tokens: int,
    reserved_cost_micros: int,
) -> None:
    if scoped_budget is None:
        if usage is not None or reservation is not None:
            raise ProviderBudgetError(
                "provider_budget_atomic_binding_mismatch"
            )
        return
    if (
        usage is None
        or reservation is None
        or usage.limits != scoped_budget.limits
    ):
        if usage is None and reservation is None:
            raise ProviderBudgetError(
                "provider_scoped_budget_migration_required"
            )
        raise ProviderBudgetError("provider_budget_atomic_binding_mismatch")
    _assert_reservation_binding(
        reservation,
        policy_version=policy_version,
        reserved_tokens=reserved_tokens,
        reserved_cost_micros=reserved_cost_micros,
    )


def _snapshot(
    tenant_id: str,
    run_id: str,
    policy_version: str,
    reservation_id: str,
    usage: _Usage,
    reservation: _Reservation,
    *,
    profile_usage: _ProfileAttemptUsage | None = None,
    scoped_usage: _Usage | None = None,
) -> ProviderBudgetSnapshot:
    overrun = bool(
        usage.limits.maximum_tokens and usage.tokens > usage.limits.maximum_tokens
    )
    scoped_overrun = bool(
        scoped_usage is not None
        and (
            (
                scoped_usage.limits.maximum_tokens
                and scoped_usage.tokens
                > scoped_usage.limits.maximum_tokens
            )
            or (
                scoped_usage.limits.maximum_cost_micros
                and scoped_usage.cost_micros
                > scoped_usage.limits.maximum_cost_micros
            )
        )
    )
    return ProviderBudgetSnapshot(
        tenant_id=tenant_id,
        run_id=run_id,
        policy_version=policy_version,
        attempts=usage.attempts,
        tokens=usage.tokens,
        cost_micros=usage.cost_micros,
        limits=usage.limits,
        reservation_id=reservation_id,
        reserved_tokens=reservation.reserved_tokens,
        reserved_cost_micros=reservation.reserved_cost_micros,
        reconciled=reservation.actual_total_tokens is not None,
        reason_code=(
            "provider_scoped_budget_overrun_recorded"
            if scoped_overrun
            else "provider_budget_overrun_recorded"
            if overrun
            else (
                "provider_budget_reconciled"
                if reservation.actual_total_tokens is not None
                else "provider_budget_reserved"
            )
        ),
        profile_attempts=(
            profile_usage.used if profile_usage is not None else None
        ),
        profile_maximum_attempts=(
            profile_usage.maximum if profile_usage is not None else None
        ),
        scoped_tokens=(
            scoped_usage.tokens if scoped_usage is not None else None
        ),
        scoped_cost_micros=(
            scoped_usage.cost_micros
            if scoped_usage is not None
            else None
        ),
        scoped_budget_overrun=scoped_overrun,
    )


__all__ = [
    "PROVIDER_BUDGET_RECEIPT_SCHEMA",
    "InMemoryProviderBudgetStore",
    "ProviderAttemptScope",
    "ProviderBudgetError",
    "ProviderBudgetLimits",
    "ProviderBudgetSnapshot",
    "ProviderBudgetStore",
    "ProviderProfileAttemptReservation",
    "ProviderScopedBudgetReservation",
]
