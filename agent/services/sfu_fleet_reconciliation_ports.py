"""Segregated runtime ports used by the durable Fleet reconciler adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.sfu_fleet_reconciliation_service import SfuFleetReconciliationItem


@dataclass(frozen=True, slots=True)
class SfuFleetRuntimeRouteObservation:
    active: bool
    route_version: int
    control_plane_consistent: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.active, bool)
            or isinstance(self.route_version, bool)
            or not isinstance(self.route_version, int)
            or self.route_version < 0
            or not isinstance(self.control_plane_consistent, bool)
        ):
            raise ValueError("sfu_fleet_runtime_observation_invalid")


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
    "SfuFleetRuntimeRouteObservation",
    "SfuFleetRuntimeRouteStatePort",
]
