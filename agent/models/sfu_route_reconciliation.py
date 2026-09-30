"""Value types of the Hub-owned, fenced SFU route reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.models.sfu_broadcast_route import (
    RouteKeyV1,
    RouteMutationResultV1,
    RouteProjectionV1,
    RouteVersionV1,
)


class ReconciliationPhase(str, Enum):
    REVOKE = "revoke"
    ENSURE = "ensure"


class ReconciliationDesiredState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    TOMBSTONED = "tombstoned"
    UNKNOWN = "unknown"


class ReconciliationAction(str, Enum):
    CONVERGED = "converged"
    APPLIED = "applied"
    UPDATED = "updated"
    REVOKED = "revoked"
    DEFERRED = "deferred"
    FAILED = "failed"


class ReconciliationRunStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BUSY = "busy"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RouteReconciliationScope:
    tenant_ref: str
    room_ref: str


@dataclass(frozen=True, slots=True)
class RouteReconciliationCursor:
    phase: ReconciliationPhase
    token: str | None = None


@dataclass(frozen=True, slots=True)
class RouteReconciliationLease:
    scope: RouteReconciliationScope
    owner_ref: str
    fencing_token: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class RouteReconciliationCandidate:
    candidate_ref: str
    key: RouteKeyV1
    phase: ReconciliationPhase
    resume_cursor: str | None


@dataclass(frozen=True, slots=True)
class RouteReconciliationPage:
    items: tuple[RouteReconciliationCandidate, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RouteReconciliationAuthority:
    """Atomically revalidated Hub state after runtime observation."""

    candidate_ref: str
    key: RouteKeyV1
    desired_state: ReconciliationDesiredState
    desired: RouteProjectionV1 | None
    expected_version: RouteVersionV1 | None
    revoke_version: RouteVersionV1 | None
    operation_id: str
    lease_fencing_token: str
    authorized: bool
    parent_active: bool
    epochs_current: bool
    route_fencing_current: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class RouteReconciliationItemOutcome:
    candidate_ref: str
    key: RouteKeyV1
    action: ReconciliationAction
    reason_code: str
    retryable: bool
    mutation: RouteMutationResultV1 | None = None


@dataclass(frozen=True, slots=True)
class SfuRouteReconciliationScopeCandidate:
    scope: RouteReconciliationScope
    cursor_after: str


@dataclass(frozen=True, slots=True)
class SfuRouteReconciliationScopePage:
    items: tuple[SfuRouteReconciliationScopeCandidate, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SfuRouteReconciliationProjectionState:
    desired_state: ReconciliationDesiredState
    desired: RouteProjectionV1 | None
    operation_id: str
    authorized: bool
    parent_active: bool
    epochs_current: bool
    route_fencing_current: bool
    reason_code: str


__all__ = [
    "ReconciliationAction",
    "ReconciliationDesiredState",
    "ReconciliationPhase",
    "ReconciliationRunStatus",
    "RouteReconciliationAuthority",
    "RouteReconciliationCandidate",
    "RouteReconciliationCursor",
    "RouteReconciliationItemOutcome",
    "RouteReconciliationLease",
    "RouteReconciliationPage",
    "RouteReconciliationScope",
    "SfuRouteReconciliationProjectionState",
    "SfuRouteReconciliationScopeCandidate",
    "SfuRouteReconciliationScopePage",
]
