"""Hub-authoritative projection seam for durable route reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.sfu_broadcast_repository_ports import SfuFanoutRoute
from agent.services.sfu_broadcast_route_port import RouteProjectionV1
from agent.services.sfu_fanout_reconciliation_service import (
    ReconciliationDesiredState,
)


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


class SfuRouteReconciliationProjectionPort(Protocol):
    def resolve(
        self, *, route: SfuFanoutRoute, now_ms: int
    ) -> SfuRouteReconciliationProjectionState: ...


__all__ = [
    "SfuRouteReconciliationProjectionPort",
    "SfuRouteReconciliationProjectionState",
]
