"""Collaborators of the autonomous loop and their single per-application seam.

:class:`.autopilot_worker_forwarding.AutopilotWorkerForwarder` applies retry,
deadline and backoff policy; the raw HTTP call to a worker, the repository
registry and the task trace sink are the collaborators in this bundle. The
autonomous loop resolves them from the application it is bound to, so tests
replace them per application with
``AUTOPILOT_LOOP_DEPENDENCIES.install(app, forward_to_worker=fake)`` instead of
monkeypatching attributes of :mod:`agent.routes.tasks.autopilot`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.routes.tasks.utils import _forward_to_worker
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services


@dataclass(frozen=True)
class AutopilotLoopDependencies:
    """The worker transport, repositories and trace sink the loop delegates to."""

    forward_to_worker: Callable[..., Any]
    append_trace_event: Callable[..., None]
    repository_registry: Callable[..., Any]


def append_trace_event_via_core_services(task_id: str, event_type: str, **data: Any) -> None:
    """Record one task trace event through the current application's services."""

    get_core_services().autopilot_support_service.append_trace_event(task_id, event_type, **data)


def production_loop_dependencies() -> AutopilotLoopDependencies:
    return AutopilotLoopDependencies(
        forward_to_worker=_forward_to_worker,
        append_trace_event=append_trace_event_via_core_services,
        repository_registry=get_repository_registry,
    )


AUTOPILOT_LOOP_DEPENDENCIES: RouteDependencySeam[AutopilotLoopDependencies] = RouteDependencySeam(
    "ananta.autopilot_loop_dependencies",
    production_loop_dependencies,
)
