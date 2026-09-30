"""Organization budget value types and canonical digests (dependency-free)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


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


def organization_budget_policy_hash(limits: Iterable[OrganizationBudgetLimit]) -> str:
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
    "OrganizationBudgetDecision",
    "OrganizationBudgetLimit",
    "OrganizationBudgetRequest",
    "OrganizationBudgetUsage",
    "organization_budget_policy_hash",
    "organization_budget_request_digest",
    "organization_budget_settlement_digest",
]
