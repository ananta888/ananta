"""Hub-authoritative projection seam for durable route reconciliation."""

from __future__ import annotations

from typing import Protocol

from agent.models.sfu_broadcast_projection import SfuFanoutRoute
from agent.models.sfu_route_reconciliation import SfuRouteReconciliationProjectionState


class SfuRouteReconciliationProjectionPort(Protocol):
    def resolve(
        self, *, route: SfuFanoutRoute, now_ms: int
    ) -> SfuRouteReconciliationProjectionState: ...


__all__ = [
    "SfuRouteReconciliationProjectionPort",
]
