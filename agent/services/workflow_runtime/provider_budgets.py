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
        if self.maximum_attempts < 1 or min(
            self.maximum_tokens,
            self.maximum_cost_micros,
        ) < 0:
            raise ProviderBudgetError("provider_budget_limits_invalid")


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
    schema: str = PROVIDER_BUDGET_RECEIPT_SCHEMA

    def to_dict(self) -> dict[str, object]:
        return {
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
    ) -> ProviderBudgetSnapshot: ...

    def reconcile(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        actual_total_tokens: int,
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


class InMemoryProviderBudgetStore:
    """Thread-safe contract adapter used by deterministic tests."""

    def __init__(self) -> None:
        self._usage: dict[tuple[str, str, str], _Usage] = {}
        self._reservations: dict[tuple[str, str, str], _Reservation] = {}
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
    ) -> ProviderBudgetSnapshot:
        _validate_binding(tenant_id, run_id, policy_version, reservation_id)
        _validate_reservation(limits, reserved_tokens, reserved_cost_micros)
        usage_key = (tenant_id, run_id, policy_version)
        reservation_key = (tenant_id, run_id, reservation_id)
        with self._lock:
            usage = self._usage.get(usage_key)
            if usage is None:
                usage = _Usage(limits=limits)
                self._usage[usage_key] = usage
            elif usage.limits != limits:
                raise ProviderBudgetError("provider_budget_limits_mismatch")
            existing = self._reservations.get(reservation_key)
            if existing is not None:
                _assert_reservation_binding(
                    existing,
                    policy_version=policy_version,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_micros=reserved_cost_micros,
                )
                return _snapshot(
                    tenant_id,
                    run_id,
                    policy_version,
                    reservation_id,
                    usage,
                    existing,
                )
            _assert_capacity(usage, reserved_tokens, reserved_cost_micros)
            reservation = _Reservation(
                policy_version=policy_version,
                reserved_tokens=reserved_tokens,
                reserved_cost_micros=reserved_cost_micros,
            )
            self._reservations[reservation_key] = reservation
            usage.attempts += 1
            usage.tokens += reserved_tokens
            usage.cost_micros += reserved_cost_micros
            return _snapshot(
                tenant_id,
                run_id,
                policy_version,
                reservation_id,
                usage,
                reservation,
            )

    def reconcile(
        self,
        *,
        tenant_id: str,
        run_id: str,
        policy_version: str,
        reservation_id: str,
        actual_total_tokens: int,
    ) -> ProviderBudgetSnapshot:
        _validate_binding(tenant_id, run_id, policy_version, reservation_id)
        if actual_total_tokens < 0:
            raise ProviderBudgetError("provider_budget_actual_tokens_invalid")
        with self._lock:
            usage = self._usage.get((tenant_id, run_id, policy_version))
            reservation = self._reservations.get((tenant_id, run_id, reservation_id))
            if usage is None or reservation is None:
                raise ProviderBudgetError("provider_budget_reservation_not_found")
            if reservation.policy_version != policy_version:
                raise ProviderBudgetError("provider_budget_reservation_binding_mismatch")
            if reservation.actual_total_tokens is not None:
                if reservation.actual_total_tokens != actual_total_tokens:
                    raise ProviderBudgetError("provider_budget_reconciliation_conflict")
                return _snapshot(
                    tenant_id,
                    run_id,
                    policy_version,
                    reservation_id,
                    usage,
                    reservation,
                )
            usage.tokens = max(
                0,
                usage.tokens + actual_total_tokens - reservation.reserved_tokens,
            )
            reservation.actual_total_tokens = actual_total_tokens
            return _snapshot(
                tenant_id,
                run_id,
                policy_version,
                reservation_id,
                usage,
                reservation,
            )


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
    if usage.attempts >= usage.limits.maximum_attempts:
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


def _snapshot(
    tenant_id: str,
    run_id: str,
    policy_version: str,
    reservation_id: str,
    usage: _Usage,
    reservation: _Reservation,
) -> ProviderBudgetSnapshot:
    overrun = bool(
        usage.limits.maximum_tokens and usage.tokens > usage.limits.maximum_tokens
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
            "provider_budget_overrun_recorded"
            if overrun
            else (
                "provider_budget_reconciled"
                if reservation.actual_total_tokens is not None
                else "provider_budget_reserved"
            )
        ),
    )


__all__ = [
    "PROVIDER_BUDGET_RECEIPT_SCHEMA",
    "InMemoryProviderBudgetStore",
    "ProviderBudgetError",
    "ProviderBudgetLimits",
    "ProviderBudgetSnapshot",
    "ProviderBudgetStore",
]
