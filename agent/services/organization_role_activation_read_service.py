"""Revision-bound, Hub-owned read model for Organization role activation.

Immutable workflow rules stay separate from live execution facts. Runtime
states are projected only from an exact persisted workflow-step binding and
strictly scoped Task, WorkerJob and lease rows; absent or conflicting evidence
remains unknown.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from agent.db_models.organizations import (
    OrganizationInstanceDB,
)
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
)
from agent.services.organization_role_activation_errors import OrganizationRoleActivationReadError
from agent.services.organization_role_activation_rows import (
    active_assignments,
    active_relations,
    active_role_slots,
    active_units,
    latest_snapshot,
    scoped_tasks,
    task_worker_jobs,
    task_worker_leases,
)
from agent.services.organization_role_activation_runtime import (
    WORKFLOW_BINDING_SCHEMA as ACTIVATION_WORKFLOW_BINDING_SCHEMA,
)
from agent.services.organization_role_activation_runtime import (
    apply_runtime_observations,
)
from agent.services.organization_role_activation_workflow import (
    ROUTER_OWNER as ACTIVATION_ROUTER_OWNER,
)
from agent.services.organization_role_activation_workflow import (
    cross_team_artifact_edges,
    project_team_workflows,
    resolve_handoff_definitions,
)


class OrganizationRoleActivationReadService:
    """Build a read-only role/workflow activation projection for one aggregate."""

    SCHEMA = "organization_role_activation_map.v1"
    ROUTER_OWNER = ACTIVATION_ROUTER_OWNER
    WORKFLOW_BINDING_SCHEMA = ACTIVATION_WORKFLOW_BINDING_SCHEMA

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory or self._default_session
        self._clock = clock or time.time

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def read(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization: OrganizationInstanceDB,
    ) -> dict[str, Any]:
        self._ensure_scope(
            tenant_id=tenant_id,
            project_id=project_id,
            organization=organization,
        )
        organization_id = organization.organization_id
        with self._session_factory() as session:
            units = active_units(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            slots = active_role_slots(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            assignments = active_assignments(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            relations = active_relations(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            tasks = scoped_tasks(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            jobs = task_worker_jobs(session, tasks=tasks)
            leases = task_worker_leases(
                session,
                tasks=tasks,
                jobs=jobs,
            )
            snapshot = latest_snapshot(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            resolver = FileCatalogDefinitionRepositoryAdapter(
                SqlOrganizationDefinitionRepository(session),
                self._catalog,
                session,
            )
            handoff_definitions = resolve_handoff_definitions(
                resolver=resolver,
                tenant_id=tenant_id,
                project_id=project_id,
                relations=relations,
            )
            teams, edges = project_team_workflows(
                resolver=resolver,
                tenant_id=tenant_id,
                project_id=project_id,
                units=[
                    unit
                    for unit in units
                    if unit.unit_kind == "team" and unit.team_blueprint_key and unit.team_blueprint_version
                ],
                slots=slots,
                assignments=assignments,
            )
            runtime_summary = apply_runtime_observations(
                teams=teams,
                tasks=tasks,
                jobs=jobs,
                leases=leases,
                organization=organization,
                now=self._clock(),
            )
            edges.extend(
                cross_team_artifact_edges(
                    teams=teams,
                    units=units,
                    relations=relations,
                    handoff_definitions=handoff_definitions,
                )
            )
            edges.sort(
                key=lambda edge: (
                    edge["type"],
                    edge["source"]["ref"],
                    edge["target"]["ref"],
                )
            )

        steps = [step for team in teams for step in team["workflow"]["steps"]]
        snapshot_stale = snapshot is None or snapshot.definition_revision != organization.definition_revision
        return {
            "schema": self.SCHEMA,
            "organization_id": organization_id,
            "definition_revision": organization.definition_revision,
            "snapshot_hash": snapshot.snapshot_hash if snapshot is not None else None,
            "snapshot_revision": snapshot.revision if snapshot is not None else None,
            "stale": snapshot_stale,
            "snapshot_reason_code": (
                "organization_role_activation_snapshot_missing"
                if snapshot is None
                else (
                    "organization_role_activation_snapshot_revision_mismatch"
                    if snapshot_stale
                    else "organization_role_activation_snapshot_current"
                )
            ),
            "router_owner": self.ROUTER_OWNER,
            "runtime_observation": runtime_summary["observation"],
            "summary": {
                "active_team_count": len(teams),
                "workflow_step_count": len(steps),
                "edge_count": len(edges),
                "unbound_step_count": sum(step["role_binding"]["state"] != "bound" for step in steps),
                "runtime_bound_step_count": runtime_summary["bound_step_count"],
                "task_ready_step_count": runtime_summary["task_ready_step_count"],
                "hub_routed_step_count": runtime_summary["hub_routed_step_count"],
                "worker_executing_step_count": runtime_summary["worker_executing_step_count"],
            },
            "teams": teams,
            "edges": edges,
        }

    @staticmethod
    def _ensure_scope(
        *,
        tenant_id: str,
        project_id: str,
        organization: OrganizationInstanceDB,
    ) -> None:
        if (
            organization.tenant_id != tenant_id
            or organization.project_id != project_id
            or not organization.organization_id
        ):
            raise OrganizationRoleActivationReadError("organization_role_activation_scope_mismatch")


__all__ = [
    "OrganizationRoleActivationReadError",
    "OrganizationRoleActivationReadService",
]
