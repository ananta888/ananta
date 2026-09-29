"""Collaborators of the autopilot task dispatch phases and their single override seam.

:func:`agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner`
resolves this bundle once per dispatch (or receives it explicitly) and hands it
to every phase through :class:`.autopilot_dispatch_context.DispatchContext`, so
the phases never look collaborators up in the coordinator's module. Tests
replace collaborators per application with
``AUTOPILOT_DISPATCH_DEPENDENCIES.override(app, ...)``; the dispatch runs inside
the loop's application context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.routes.tasks.autopilot_model_selector import _select_model_for_task
from agent.routes.tasks.autopilot_strategy_candidates import (
    _extract_strategy_state,
    _proposal_strategy_candidates,
)
from agent.services.recovery_dispatch_gate_service import get_recovery_dispatch_gate_service
from agent.services.repository_registry import get_repository_registry

from .autopilot_task_dispatcher_helpers import _current_task_status


@dataclass(frozen=True)
class DispatchDependencies:
    """Services and policies the dispatch phases delegate to."""

    current_task_status: Callable[..., Any]
    recovery_dispatch_gate: Callable[[], Any]
    repository_registry: Callable[..., Any]
    select_model_for_task: Callable[..., tuple[Any, dict[str, Any]]]
    proposal_strategy_candidates: Callable[..., list[dict[str, Any]]]
    extract_strategy_state: Callable[[Any], dict[str, Any]]


def production_dispatch_dependencies() -> DispatchDependencies:
    return DispatchDependencies(
        current_task_status=_current_task_status,
        recovery_dispatch_gate=get_recovery_dispatch_gate_service,
        repository_registry=get_repository_registry,
        select_model_for_task=_select_model_for_task,
        proposal_strategy_candidates=_proposal_strategy_candidates,
        extract_strategy_state=_extract_strategy_state,
    )


AUTOPILOT_DISPATCH_DEPENDENCIES: RouteDependencySeam[DispatchDependencies] = RouteDependencySeam(
    "ananta.autopilot_dispatch_dependencies",
    production_dispatch_dependencies,
)
