"""Value types of the Hub-owned, lease/fencing protected SFU fleet reconciliation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationItem:
    item_id: str
    cursor_after: str
    expected_state_version: int
    desired_route: bool
    desired_route_version: int
    active_route: bool
    active_route_version: int
    route_intent_expires_at_ms: int
    reservation_active: bool
    reservation_orphaned: bool
    reservation_expires_at_ms: int
    observation_fresh_until_ms: int
    stale_access_expires_at_ms: int
    node_health: str
    admission_ready: bool
    control_plane_consistent: bool


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationPage:
    items: tuple[SfuFleetReconciliationItem, ...]
    next_cursor: str | None


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


__all__ = [
    "SfuFleetReconciliationItem",
    "SfuFleetReconciliationPage",
    "SfuFleetRuntimeRouteObservation",
]
