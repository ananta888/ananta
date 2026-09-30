"""Collaborators of the Organization planning routes and their single override seam.

The planning handlers in :mod:`agent.routes.organization_planning` reach the
planning composition, the worker-result capability verifier, the callback
proposal ingress and the operator-principal resolution only through this
bundle. Tests replace collaborators per application with
``ORGANIZATION_PLANNING_ROUTE_DEPENDENCIES.install(app, composition=...)``
instead of monkeypatching attributes of the route module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import g

from agent.routes.organization_route_support import (
    request_principal,
    require_organization_scope,
)
from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.organization_planning_composition import (
    get_organization_planning_composition,
)
from agent.services.project_access_authority import ProjectCapability
from agent.services.worker_result_capability_service import (
    WorkerResultCapabilityService,
)
from agent.services.worker_task_proposal_result_adapter import (
    ingest_callback_task_proposals,
)


def operator_principal(
    organization_id: str,
    capability: ProjectCapability,
) -> OrganizationAccessPrincipal:
    """The authenticated operator, scoped to the organization and capability."""

    route_principal = request_principal()
    scope = require_organization_scope(organization_id, capability)
    identity = (getattr(g, "user", {}) or {}) or (getattr(g, "auth_payload", {}) or {})
    credential_type = str(identity.get("credential_type") or "user")
    return OrganizationAccessPrincipal(
        principal_id=route_principal.subject_id,
        tenant_id=scope.tenant_id,
        credential_type=credential_type,
        project_id=scope.project_id,
    )


@dataclass(frozen=True)
class OrganizationPlanningRouteDependencies:
    """Services the Organization planning routes delegate to."""

    composition: Callable[[], Any]
    worker_result_capabilities: Callable[[], Any]
    ingest_task_proposals: Callable[..., Any]
    operator_principal: Callable[[str, ProjectCapability], OrganizationAccessPrincipal]


def production_organization_planning_route_dependencies() -> OrganizationPlanningRouteDependencies:
    return OrganizationPlanningRouteDependencies(
        composition=get_organization_planning_composition,
        worker_result_capabilities=WorkerResultCapabilityService,
        ingest_task_proposals=ingest_callback_task_proposals,
        operator_principal=operator_principal,
    )


ORGANIZATION_PLANNING_ROUTE_DEPENDENCIES: RouteDependencySeam[OrganizationPlanningRouteDependencies] = (
    RouteDependencySeam(
        "ananta.organization_planning_route_dependencies",
        production_organization_planning_route_dependencies,
    )
)
