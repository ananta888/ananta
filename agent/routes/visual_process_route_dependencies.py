"""Collaborators of the visual-process workflow routes and their single override seam.

The workflow request/start/status/command/event handlers delegate backend
selection, ownership checks, route authorization, error envelopes and the
caseflow trace read model to these collaborators. Handlers obtain them via
:func:`visual_process_route_dependencies`; tests replace them per application
with ``VISUAL_PROCESS_ROUTE_DEPENDENCIES.override(app, ...)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.routes.workflow_control_security import (
    backend_error,
    configured_workflow_backend,
    require_workflow_owner,
)
from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    get_caseflow_agent_collaboration_trace_projection_service,
)
from agent.services.workflow_route_authorization_service import workflow_route_authorization_service


@dataclass(frozen=True)
class VisualProcessRouteDependencies:
    """Services and policies the visual-process workflow routes delegate to."""

    configured_workflow_backend: Callable[[Any], tuple[Any, Any]]
    require_workflow_owner: Callable[[str], tuple[Any, Any]]
    backend_error: Callable[..., Any]
    workflow_route_authorization_service: Any
    get_caseflow_agent_collaboration_trace_projection_service: Callable[[], Any]


def production_visual_process_route_dependencies() -> VisualProcessRouteDependencies:
    return VisualProcessRouteDependencies(
        configured_workflow_backend=configured_workflow_backend,
        require_workflow_owner=require_workflow_owner,
        backend_error=backend_error,
        workflow_route_authorization_service=workflow_route_authorization_service,
        get_caseflow_agent_collaboration_trace_projection_service=(
            get_caseflow_agent_collaboration_trace_projection_service
        ),
    )


VISUAL_PROCESS_ROUTE_DEPENDENCIES: RouteDependencySeam[VisualProcessRouteDependencies] = RouteDependencySeam(
    "ananta.visual_process_route_dependencies",
    production_visual_process_route_dependencies,
)


def visual_process_route_dependencies() -> VisualProcessRouteDependencies:
    """The visual-process collaborators of the current application."""

    return VISUAL_PROCESS_ROUTE_DEPENDENCIES.resolve()
