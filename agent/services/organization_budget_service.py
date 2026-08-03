"""Atomic hierarchical budgets for organization-controlled execution."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Iterable, Protocol


@dataclass(frozen=True, slots=True)
class OrganizationBudgetLimit:
    scope_kind: str
    scope_id: str
    max_tokens: int
    max_cost: Decimal
    max_wall_seconds: int
    max_parallelism: int
    revision: str


@dataclass(frozen=True, slots=True)
class OrganizationBudgetRequest:
    reservation_id: str
    organization_id: str
    unit_id: str | None
    team_id: str | None
    workflow_id: str | None
    task_id: str
    tokens: int
    cost: Decimal
    wall_seconds: int
    parallel_slots: int
    model_profile: str


@dataclass(frozen=True, slots=True)
class OrganizationBudgetUsage:
    tokens: int = 0
    cost: Decimal = Decimal("0")
    wall_seconds: int = 0
    parallel_slots: int = 0


@dataclass(frozen=True, slots=True)
class OrganizationBudgetDecision:
    allowed: bool
    reason_code: str
    reservation_id: str
    policy_hash: str
    exceeded_scopes: tuple[str, ...]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _InMemoryBudgetReservation:
    request_digest: str
    policy_hash: str
    request: OrganizationBudgetRequest
    limits: tuple[OrganizationBudgetLimit, ...]
    decision: OrganizationBudgetDecision
    settlement_digest: str | None = None


class OrganizationBudgetLedgerPort(Protocol):
    def reserve(
        self,
        *,
        request: OrganizationBudgetRequest,
        limits: tuple[OrganizationBudgetLimit, ...],
        policy_hash: str,
    ) -> OrganizationBudgetDecision: ...

    def settle(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal,
        actual_wall_seconds: int,
    ) -> bool: ...


class OrganizationBudgetService:
    """Validates policies and delegates one atomic reservation to a ledger."""

    _SCOPE_KINDS = frozenset({"organization", "unit", "team", "workflow", "task"})

    def __init__(self, *, ledger: OrganizationBudgetLedgerPort) -> None:
        self._ledger = ledger

    def reserve(
        self,
        *,
        request: OrganizationBudgetRequest,
        limits: Iterable[OrganizationBudgetLimit],
    ) -> OrganizationBudgetDecision:
        rows = tuple(limits)
        issues = self._validate(request, rows)
        policy_hash = self.policy_hash(rows)
        if issues:
            return OrganizationBudgetDecision(
                allowed=False,
                reason_code=issues[0],
                reservation_id=request.reservation_id,
                policy_hash=policy_hash,
                exceeded_scopes=tuple(issues),
            )
        return self._ledger.reserve(request=request, limits=rows, policy_hash=policy_hash)

    def settle(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal | str,
        actual_wall_seconds: int,
    ) -> bool:
        cost = _decimal(actual_cost)
        if not reservation_id or actual_tokens < 0 or cost < 0 or actual_wall_seconds < 0:
            return False
        return self._ledger.settle(
            reservation_id=reservation_id,
            actual_tokens=actual_tokens,
            actual_cost=cost,
            actual_wall_seconds=actual_wall_seconds,
        )

    @staticmethod
    def estimate_blueprint(
        *,
        team_role_slot_counts: dict[str, int],
        model_profile_costs: dict[str, Decimal | str],
        parallel_team_ids: Iterable[str],
        shared_team_ids: Iterable[str],
    ) -> dict[str, object]:
        normalized_slots = {str(team_id): max(0, int(count)) for team_id, count in team_role_slot_counts.items()}
        costs = {key: _decimal(value) for key, value in model_profile_costs.items()}
        default_cost = costs.get("default", Decimal("0"))
        agent_slots = sum(normalized_slots.values())
        estimated_cost = sum(default_cost * count for count in normalized_slots.values())
        parallel = tuple(dict.fromkeys(str(value) for value in parallel_team_ids if str(value)))
        shared = tuple(dict.fromkeys(str(value) for value in shared_team_ids if str(value)))
        bottlenecks = sorted(team_id for team_id in shared if normalized_slots.get(team_id, 0) <= 1)
        return {
            "agent_slots": agent_slots,
            "estimated_parallelism": len(parallel),
            "estimated_cost": str(estimated_cost),
            "model_profiles": sorted(costs),
            "shared_team_bottlenecks": bottlenecks,
        }

    @classmethod
    def policy_hash(cls, limits: Iterable[OrganizationBudgetLimit]) -> str:
        payload = [
            {
                "scope_kind": row.scope_kind,
                "scope_id": row.scope_id,
                "max_tokens": row.max_tokens,
                "max_cost": _canonical_decimal(row.max_cost),
                "max_wall_seconds": row.max_wall_seconds,
                "max_parallelism": row.max_parallelism,
                "revision": row.revision,
            }
            for row in sorted(limits, key=lambda item: (item.scope_kind, item.scope_id, item.revision))
        ]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def _validate(
        cls,
        request: OrganizationBudgetRequest,
        limits: tuple[OrganizationBudgetLimit, ...],
    ) -> list[str]:
        issues: list[str] = []
        if (
            not request.reservation_id
            or not request.organization_id
            or not request.task_id
            or not request.model_profile
        ):
            issues.append("budget_request_binding_missing")
        if request.tokens < 0 or request.cost < 0 or request.wall_seconds < 0 or request.parallel_slots <= 0:
            issues.append("budget_request_values_invalid")
        if not limits:
            issues.append("budget_limits_missing")
        seen: set[tuple[str, str]] = set()
        bound_scope_ids = {
            "organization": request.organization_id,
            "unit": request.unit_id,
            "team": request.team_id,
            "workflow": request.workflow_id,
            "task": request.task_id,
        }
        for limit in limits:
            key = (limit.scope_kind, limit.scope_id)
            if limit.scope_kind not in cls._SCOPE_KINDS or not limit.scope_id or not limit.revision:
                issues.append("budget_limit_binding_invalid")
            if key in seen:
                issues.append(f"budget_limit_duplicate:{limit.scope_kind}:{limit.scope_id}")
            seen.add(key)
            expected_scope_id = bound_scope_ids.get(limit.scope_kind)
            if not expected_scope_id or limit.scope_id != expected_scope_id:
                issues.append(f"budget_limit_scope_mismatch:{limit.scope_kind}:{limit.scope_id}")
            if min(limit.max_tokens, limit.max_wall_seconds, limit.max_parallelism) < 0 or limit.max_cost < 0:
                issues.append(f"budget_limit_values_invalid:{limit.scope_kind}:{limit.scope_id}")
        return sorted(set(issues))


class InMemoryOrganizationBudgetLedger:
    """Thread-safe deterministic adapter for development and focused tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[tuple[str, str], OrganizationBudgetUsage] = {}
        self._reservations: dict[str, _InMemoryBudgetReservation] = {}

    def reserve(
        self,
        *,
        request: OrganizationBudgetRequest,
        limits: tuple[OrganizationBudgetLimit, ...],
        policy_hash: str,
    ) -> OrganizationBudgetDecision:
        request_digest = organization_budget_request_digest(request)
        with self._lock:
            existing = self._reservations.get(request.reservation_id)
            if existing:
                if existing.request_digest == request_digest and existing.policy_hash == policy_hash:
                    return replace(existing.decision, replayed=True)
                return OrganizationBudgetDecision(
                    False, "budget_reservation_conflict", request.reservation_id, policy_hash, ()
                )
            exceeded: list[str] = []
            for limit in limits:
                usage = self._usage.get((limit.scope_kind, limit.scope_id), OrganizationBudgetUsage())
                if usage.tokens + request.tokens > limit.max_tokens:
                    exceeded.append(f"{limit.scope_kind}:{limit.scope_id}:tokens")
                if usage.cost + request.cost > limit.max_cost:
                    exceeded.append(f"{limit.scope_kind}:{limit.scope_id}:cost")
                if usage.wall_seconds + request.wall_seconds > limit.max_wall_seconds:
                    exceeded.append(f"{limit.scope_kind}:{limit.scope_id}:time")
                if usage.parallel_slots + request.parallel_slots > limit.max_parallelism:
                    exceeded.append(f"{limit.scope_kind}:{limit.scope_id}:parallelism")
            if exceeded:
                decision = OrganizationBudgetDecision(
                    False, "organization_budget_exhausted", request.reservation_id, policy_hash, tuple(sorted(exceeded))
                )
                self._reservations[request.reservation_id] = _InMemoryBudgetReservation(
                    request_digest=request_digest,
                    policy_hash=policy_hash,
                    request=request,
                    limits=limits,
                    decision=decision,
                )
                return decision
            for limit in limits:
                key = (limit.scope_kind, limit.scope_id)
                usage = self._usage.get(key, OrganizationBudgetUsage())
                self._usage[key] = OrganizationBudgetUsage(
                    tokens=usage.tokens + request.tokens,
                    cost=usage.cost + request.cost,
                    wall_seconds=usage.wall_seconds + request.wall_seconds,
                    parallel_slots=usage.parallel_slots + request.parallel_slots,
                )
            decision = OrganizationBudgetDecision(
                True, "organization_budget_reserved", request.reservation_id, policy_hash, ()
            )
            self._reservations[request.reservation_id] = _InMemoryBudgetReservation(
                request_digest=request_digest,
                policy_hash=policy_hash,
                request=request,
                limits=limits,
                decision=decision,
            )
            return decision

    def settle(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal,
        actual_wall_seconds: int,
    ) -> bool:
        # Focused adapter records final values on the reservation only. A SQL
        # ledger can reconcile scope aggregates transactionally at settlement.
        with self._lock:
            existing = self._reservations.get(reservation_id)
            if not existing or not existing.decision.allowed:
                return False
            settlement_digest = organization_budget_settlement_digest(
                actual_tokens=actual_tokens,
                actual_cost=actual_cost,
                actual_wall_seconds=actual_wall_seconds,
            )
            if existing.settlement_digest is not None:
                return existing.settlement_digest == settlement_digest
            request = existing.request
            limits = existing.limits
            for limit in limits:
                key = (limit.scope_kind, limit.scope_id)
                usage = self._usage.get(key, OrganizationBudgetUsage())
                self._usage[key] = OrganizationBudgetUsage(
                    tokens=max(0, usage.tokens - request.tokens + actual_tokens),
                    cost=max(Decimal("0"), usage.cost - request.cost + actual_cost),
                    wall_seconds=max(0, usage.wall_seconds - request.wall_seconds + actual_wall_seconds),
                    parallel_slots=max(0, usage.parallel_slots - request.parallel_slots),
                )
            settled = replace(
                request,
                tokens=actual_tokens,
                cost=actual_cost,
                wall_seconds=actual_wall_seconds,
                parallel_slots=0,
            )
            self._reservations[reservation_id] = _InMemoryBudgetReservation(
                request_digest=existing.request_digest,
                policy_hash=existing.policy_hash,
                request=settled,
                limits=limits,
                decision=existing.decision,
                settlement_digest=settlement_digest,
            )
            return True


def _decimal(value: Decimal | str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("budget_decimal_invalid") from exc
    if not result.is_finite():
        raise ValueError("budget_decimal_invalid")
    return result


def organization_budget_request_digest(request: OrganizationBudgetRequest) -> str:
    payload = {
        "reservation_id": request.reservation_id,
        "organization_id": request.organization_id,
        "unit_id": request.unit_id,
        "team_id": request.team_id,
        "workflow_id": request.workflow_id,
        "task_id": request.task_id,
        "tokens": request.tokens,
        "cost": _canonical_decimal(request.cost),
        "wall_seconds": request.wall_seconds,
        "parallel_slots": request.parallel_slots,
        "model_profile": request.model_profile,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def organization_budget_settlement_digest(
    *,
    actual_tokens: int,
    actual_cost: Decimal,
    actual_wall_seconds: int,
) -> str:
    payload = {
        "actual_tokens": actual_tokens,
        "actual_cost": _canonical_decimal(actual_cost),
        "actual_wall_seconds": actual_wall_seconds,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


__all__ = [
    "InMemoryOrganizationBudgetLedger",
    "OrganizationBudgetDecision",
    "OrganizationBudgetLedgerPort",
    "OrganizationBudgetLimit",
    "OrganizationBudgetRequest",
    "OrganizationBudgetService",
    "OrganizationBudgetUsage",
    "organization_budget_request_digest",
    "organization_budget_settlement_digest",
]
