"""Collaborators of the control-center routes (``/api``) and their single override seam.

The control-center handlers reach persistence and share sessions only through
this bundle. Handlers resolve it per request; tests replace collaborators per
application with ``CONTROL_CENTER_ROUTE_DEPENDENCIES.install(app, ...)`` (or
``override``) instead of monkeypatching attributes of
:mod:`agent.routes.control_center_api`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.services.repository_registry import get_repository_registry
from agent.services.share_session_service import get_share_session_service


@dataclass(frozen=True)
class ControlCenterRouteDependencies:
    """Repositories and services the control-center routes delegate to."""

    repository_registry: Callable[[], Any]
    share_session_service: Callable[[], Any]


def production_control_center_route_dependencies() -> ControlCenterRouteDependencies:
    return ControlCenterRouteDependencies(
        repository_registry=get_repository_registry,
        share_session_service=get_share_session_service,
    )


CONTROL_CENTER_ROUTE_DEPENDENCIES: RouteDependencySeam[ControlCenterRouteDependencies] = RouteDependencySeam(
    "ananta.control_center_route_dependencies",
    production_control_center_route_dependencies,
)
