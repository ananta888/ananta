"""Segregated runtime ports used by the durable Fleet reconciler adapters."""

from __future__ import annotations

from typing import Protocol

from agent.models.sfu_fleet_reconciliation import (
    SfuFleetReconciliationItem,
    SfuFleetRuntimeRouteObservation,
)


class SfuFleetRuntimeRouteStatePort(Protocol):
    def observe(
        self,
        *,
        tenant_id: str,
        room_id: str,
        route_id: str,
        desired_route_version: int,
    ) -> SfuFleetRuntimeRouteObservation: ...


class SfuFleetRuntimeRouteMutationPort(Protocol):
    def fence_route(
        self,
        *,
        item: SfuFleetReconciliationItem,
        fencing_token: int,
        reason_code: str,
    ) -> bool: ...

    def reconcile_desired_route(
        self,
        *,
        item: SfuFleetReconciliationItem,
        fencing_token: int,
        access_expires_at_ms: int,
    ) -> bool: ...


__all__ = [
    "SfuFleetRuntimeRouteMutationPort",
    "SfuFleetRuntimeRouteStatePort",
]
