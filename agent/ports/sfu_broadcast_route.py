"""Segregated runtime operation ports for Hub-authorized SFU broadcast routes.

The four protocols deliberately remain separate so consumers only depend on the
operation they need.  Their value types live in
:mod:`agent.models.sfu_broadcast_route`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.models.sfu_broadcast_route import (
    ApplyRouteCommandV1,
    ObserveRouteQueryV1,
    RevokeRouteCommandV1,
    RouteMutationResultV1,
    RouteObservationResultV1,
    UpdateRouteCommandV1,
)


@runtime_checkable
class ApplyRoutePortV1(Protocol):
    def apply(self, command: ApplyRouteCommandV1) -> RouteMutationResultV1:
        """Apply one absolute projection if the route is absent."""


@runtime_checkable
class UpdateRoutePortV1(Protocol):
    def update(self, command: UpdateRouteCommandV1) -> RouteMutationResultV1:
        """Atomically replace an active route after exact Hub-fence matching."""


@runtime_checkable
class RevokeRoutePortV1(Protocol):
    def revoke(self, command: RevokeRouteCommandV1) -> RouteMutationResultV1:
        """Atomically remove an active route and retain its Hub tombstone."""


@runtime_checkable
class ObserveRoutePortV1(Protocol):
    def observe(self, query: ObserveRouteQueryV1) -> RouteObservationResultV1:
        """Observe only the exact runtime projection or its absence."""


__all__ = [
    "ApplyRoutePortV1",
    "ObserveRoutePortV1",
    "RevokeRoutePortV1",
    "UpdateRoutePortV1",
]
