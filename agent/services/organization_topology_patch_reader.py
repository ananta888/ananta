"""Constant-query SQL read adapter that loads the state a topology patch is evaluated against."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlmodel import Session, select

from agent.db_models.agents import AgentInfoDB
from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationHandoffDefinitionRevisionDB,
    OrganizationPolicyRevisionDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)
from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerSlotLeaseDB
from agent.models.organization_models import (
    TeamBlueprintDefinition,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_topology_patch_contracts import (
    OrganizationPatchState,
)

_TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled", "archived", "rejected"})


class SqlOrganizationPatchReadAdapter:
    """Constant-query read adapter used by preview and apply revalidation."""

    def __init__(self, *, session_factory=None, catalog=None) -> None:
        self._session_factory = session_factory or self._default_session
        self._catalog = catalog

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def load_state(self, **kwargs) -> OrganizationPatchState | None:
        supplied_session = kwargs.pop("session", None)
        if supplied_session is not None:
            return self._load(supplied_session, **kwargs)
        with self._session_factory() as session:
            return self._load(session, **kwargs)

    def _load(
        self,
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        agent_ids: set[str],
        for_update: bool = False,
    ) -> OrganizationPatchState | None:
        from agent.db_models.organizations import OrganizationInstanceDB

        statement = (
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.organization_id == organization_id)
        )
        if for_update:
            statement = statement.with_for_update()
        organization = session.exec(statement).first()
        if organization is None:
            return None

        def scoped(model):
            query = (
                select(model)
                .where(model.tenant_id == tenant_id)
                .where(model.project_id == project_id)
                .where(model.organization_id == organization_id)
            )
            if for_update:
                query = query.with_for_update()
            return tuple(session.exec(query).all())

        units = scoped(OrganizationUnitDB)
        links = scoped(OrganizationTeamLinkDB)
        slots = scoped(OrganizationRoleSlotDB)
        assignments = scoped(OrganizationRoleAssignmentDB)
        relations = scoped(OrganizationRelationDB)
        snapshot_query = (
            select(OrganizationTopologySnapshotDB)
            .where(OrganizationTopologySnapshotDB.tenant_id == tenant_id)
            .where(OrganizationTopologySnapshotDB.project_id == project_id)
            .where(OrganizationTopologySnapshotDB.organization_id == organization_id)
            .order_by(OrganizationTopologySnapshotDB.revision.desc())
        )
        if for_update:
            snapshot_query = snapshot_query.with_for_update()
        snapshot = session.exec(snapshot_query).first()

        database_team_rows = session.exec(
            select(TeamBlueprintRevisionDB)
            .where(TeamBlueprintRevisionDB.tenant_id == tenant_id)
            .where(TeamBlueprintRevisionDB.project_id == project_id)
        ).all()
        definitions = SqlOrganizationDefinitionRepository(session)
        if self._catalog is not None:
            definitions = FileCatalogDefinitionRepositoryAdapter(definitions, self._catalog, session)
        team_identities = {(row.definition_key, row.version) for row in database_team_rows}
        if self._catalog is not None:
            team_identities.update(self._catalog.snapshot().team_blueprints)
        team_blueprints: dict[str, TeamBlueprintDefinition] = {}
        team_row_by_ref: dict[str, Any] = {}
        for key, version in sorted(team_identities):
            row = definitions.get_team_blueprint(tenant_id, project_id, key, version)
            if row is None:
                continue
            portable_ref = f"{row.definition_key}@{row.version}"
            try:
                team_blueprints[portable_ref] = TeamBlueprintDefinition.model_validate(row.definition_json)
            except ValidationError:
                continue
            team_row_by_ref[portable_ref] = row

        role_identities = {
            (row.definition_key, row.version)
            for row in session.exec(
                select(RoleTemplateRevisionDB)
                .where(RoleTemplateRevisionDB.tenant_id == tenant_id)
                .where(RoleTemplateRevisionDB.project_id == project_id)
            ).all()
        }
        if self._catalog is not None:
            role_identities.update(self._catalog.snapshot().role_templates)
        role_refs = frozenset(f"{key}@{version}" for key, version in role_identities)
        handoff_identities = {
            (row.definition_key, row.version)
            for row in session.exec(
                select(OrganizationHandoffDefinitionRevisionDB)
                .where(OrganizationHandoffDefinitionRevisionDB.tenant_id == tenant_id)
                .where(OrganizationHandoffDefinitionRevisionDB.project_id == project_id)
                .where(OrganizationHandoffDefinitionRevisionDB.lifecycle == "active")
            ).all()
        }
        if self._catalog is not None:
            handoff_identities.update(self._catalog.snapshot().handoffs)
        handoff_refs = frozenset(f"{key}@{version}" for key, version in handoff_identities)
        workflow_identities = {
            (row.definition_key, row.version)
            for row in session.exec(
                select(WorkflowDefinitionRevisionDB)
                .where(WorkflowDefinitionRevisionDB.tenant_id == tenant_id)
                .where(WorkflowDefinitionRevisionDB.project_id == project_id)
            ).all()
        }
        if self._catalog is not None:
            workflow_identities.update(self._catalog.snapshot().workflows)
        workflow_steps = {}
        for key, version in sorted(workflow_identities):
            row = definitions.get_workflow(tenant_id, project_id, key, version)
            if row is None:
                continue
            steps = getattr(row, "steps_json", None)
            if steps is None:
                steps = (row.definition_json or {}).get("steps") or []
            workflow_steps[f"{key}@{version}"] = len(steps)
        agent_query = select(AgentInfoDB).where(AgentInfoDB.url.in_(sorted(agent_ids))) if agent_ids else None
        if agent_query is not None and for_update:
            # Serializes capacity-changing assignments for the same Agent even
            # when that Agent has no existing assignment row yet.
            agent_query = agent_query.with_for_update()
        agents = {row.url: row for row in (session.exec(agent_query).all() if agent_query is not None else [])}
        global_assignment_count_by_agent = {agent_id: 0 for agent_id in agent_ids}
        global_assignment_query = (
            select(OrganizationRoleAssignmentDB)
            .where(OrganizationRoleAssignmentDB.agent_url.in_(sorted(agent_ids)))
            .where(OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active")))
            if agent_ids
            else None
        )
        if global_assignment_query is not None and for_update:
            global_assignment_query = global_assignment_query.with_for_update()
        global_assignments = session.exec(global_assignment_query).all() if global_assignment_query is not None else []
        for assignment in global_assignments:
            global_assignment_count_by_agent[assignment.agent_url] = (
                global_assignment_count_by_agent.get(assignment.agent_url, 0) + 1
            )

        definition = definitions.get_organization_blueprint(
            tenant_id,
            project_id,
            organization.definition_key,
            organization.definition_version,
        )
        policy_rows = list(
            session.exec(
                select(OrganizationPolicyRevisionDB)
                .where(OrganizationPolicyRevisionDB.tenant_id == tenant_id)
                .where(OrganizationPolicyRevisionDB.project_id == project_id)
            ).all()
        )
        policy_hashes = {f"{row.policy_key}@{row.revision}": row.content_hash for row in policy_rows}
        if self._catalog is not None:
            for (key, version), value in self._catalog.snapshot().policies.items():
                policy_hashes.setdefault(f"{key}@{version}", canonical_definition_sha256(value))
        budget_ref = str(
            ((definition.definition_json if definition else {}).get("budgets") or {}).get("policy_ref") or ""
        )
        budget_hash = policy_hashes.get(budget_ref)
        effective_policy_hash = canonical_sha256(
            {
                "organization_definition_hash": getattr(definition, "content_hash", None),
                "policy_hashes": policy_hashes,
                "slot_assignment_policies": {
                    row.id: {
                        "assignment_policy": row.assignment_policy,
                        "separation_of_duties": row.separation_of_duties,
                    }
                    for row in slots
                },
            }
        )
        activity = self._activity(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            units=units,
            links=links,
            slots=slots,
            assignments=assignments,
            for_update=for_update,
        )
        return OrganizationPatchState(
            organization=organization,
            snapshot=snapshot,
            units=units,
            team_links=links,
            role_slots=slots,
            assignments=assignments,
            relations=relations,
            team_blueprints=team_blueprints,
            team_blueprint_rows=team_row_by_ref,
            role_template_refs=role_refs,
            workflow_steps=workflow_steps,
            agents=agents,
            global_assignment_count_by_agent=global_assignment_count_by_agent,
            activity_by_unit=activity,
            effective_policy_hash=effective_policy_hash,
            budget_policy_hash=budget_hash,
            handoff_definition_refs=handoff_refs,
        )

    @staticmethod
    def _activity(session, *, tenant_id, project_id, organization_id, units, links, slots, assignments, for_update):
        result = {row.id: {"tasks": 0, "leases": 0, "open_gates": 0, "handoffs": 0, "assignments": 0} for row in units}
        unit_ids = list(result)
        task_query = (
            (
                select(TaskDB)
                .where(TaskDB.tenant_id == tenant_id)
                .where(TaskDB.project_id == project_id)
                .where(TaskDB.organization_id == organization_id)
                .where(TaskDB.unit_id.in_(unit_ids))
            )
            if unit_ids
            else None
        )
        if task_query is not None and for_update:
            task_query = task_query.with_for_update()
        tasks = list(session.exec(task_query).all()) if task_query is not None else []
        active_tasks = [row for row in tasks if str(row.status).lower() not in _TERMINAL_TASK_STATES]
        for row in active_tasks:
            if row.unit_id in result:
                result[row.unit_id]["tasks"] += 1
                verification = dict(row.verification_status or {})
                if verification.get("status") in {"open", "pending", "blocked"} or verification.get("open_gates"):
                    result[row.unit_id]["open_gates"] += 1

        active_task_ids = [row.id for row in active_tasks]
        leases = []
        if active_task_ids:
            lease_query = (
                select(WorkerSlotLeaseDB)
                .where(WorkerSlotLeaseDB.parent_task_id.in_(active_task_ids))
                .where(WorkerSlotLeaseDB.status == "active")
            )
            if for_update:
                lease_query = lease_query.with_for_update()
            leases = list(session.exec(lease_query).all())
        unit_by_task = {row.id: row.unit_id for row in active_tasks}
        for lease in leases:
            unit_id = unit_by_task.get(lease.parent_task_id)
            if unit_id in result:
                result[unit_id]["leases"] += 1

        unit_by_slot = {row.id: row.unit_id for row in slots}
        for row in assignments:
            if row.lifecycle == "active" and unit_by_slot.get(row.role_slot_id) in result:
                result[unit_by_slot[row.role_slot_id]]["assignments"] += 1

        unit_by_team = {row.team_id: row.unit_id for row in links}
        team_ids = list(unit_by_team)
        if team_ids:
            handoff_query = (
                select(CrossTeamTaskDependencyDB)
                .where(CrossTeamTaskDependencyDB.tenant_id == tenant_id)
                .where(CrossTeamTaskDependencyDB.project_id == project_id)
                .where(CrossTeamTaskDependencyDB.organization_id == organization_id)
                .where(CrossTeamTaskDependencyDB.status.in_(["pending", "blocked", "ready"]))
            )
            if for_update:
                handoff_query = handoff_query.with_for_update()
            handoffs = session.exec(handoff_query).all()
            for row in handoffs:
                for team_id in {row.source_team_id, row.target_team_id}:
                    unit_id = unit_by_team.get(team_id)
                    if unit_id in result:
                        result[unit_id]["handoffs"] += 1
        return result


__all__ = [
    "SqlOrganizationPatchReadAdapter",
]
