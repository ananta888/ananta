"""Write-free topology patch planning and atomic Hub-owned application.

Definitions, runtime overlays and presentation data stay separate: this
service mutates normalized organization definition-instance rows only.  It
never accepts runtime edges and never writes UI layout coordinates.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlmodel import Session, select

from agent.db_models.agents import AgentInfoDB
from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationAuditOutboxDB,
    OrganizationHandoffDefinitionRevisionDB,
    OrganizationOperationDB,
    OrganizationPolicyRevisionDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologyPatchGrantDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)
from agent.db_models.tasks import TaskDB
from agent.db_models.teams import TeamDB
from agent.db_models.workers import WorkerSlotLeaseDB
from agent.models.organization_models import (
    AssignmentPolicyDefinition,
    OrganizationDiagnostic,
    OrganizationLimitProfile,
    SeparationOfDutiesDefinition,
    TeamBlueprintDefinition,
    VersionedDefinitionRef,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.ports.organization_definitions import OrganizationLimitProfilePort
from agent.repositories.organizations.adapters import SqlOrganizationLimitProfileAdapter
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.services.organization_active_work_service import (
    OrganizationActiveWorkError,
    SqlOrganizationActiveWorkService,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_blueprint_validation_service import (
    PARENT_KIND_MATRIX,
    RELATION_ENDPOINT_KIND_MATRIX,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_slot_separation_service import (
    OrganizationSlotSeparationPolicy,
    evaluate_organization_slot_separation,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork

_TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled", "archived", "rejected"})
_RELATION_KINDS = Literal[
    "governs",
    "enables",
    "supplies_research_to",
    "prototypes_for",
    "reviews",
    "releases_for",
    "declared_dependency",
    "handoff",
    "escalates_to",
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TopologyAddValue(_Closed):
    stable_key: str = Field(min_length=1, max_length=191)
    name: str = Field(min_length=1, max_length=255)
    team_blueprint_ref: str | None = None
    slot_key: str | None = None
    role_template_ref: str | None = None
    required: bool | None = None
    min_count: int | None = Field(default=None, ge=0)
    default_count: int | None = Field(default=None, ge=0)
    max_count: int | None = Field(default=None, ge=1)
    assignment_policy: AssignmentPolicyDefinition | None = None
    separation_of_duties: SeparationOfDutiesDefinition | None = None
    overlays: list[str] = Field(default_factory=list)


class TopologyAddOperation(_Closed):
    op: Literal["add"]
    node_kind: Literal["coordination_unit", "value_stream", "team", "role_slot"]
    parent_id: str = Field(min_length=1, max_length=191)
    value: TopologyAddValue

    @model_validator(mode="after")
    def validate_kind_payload(self) -> "TopologyAddOperation":
        required_slot_fields = {
            "slot_key",
            "role_template_ref",
            "required",
            "min_count",
            "default_count",
            "max_count",
            "assignment_policy",
            "separation_of_duties",
        }
        if self.node_kind == "role_slot":
            # ``max_count=null`` deliberately means unbounded, so presence and
            # value must be checked separately.
            if not required_slot_fields.issubset(self.value.model_fields_set) or any(
                getattr(self.value, field) is None for field in required_slot_fields - {"max_count"}
            ):
                raise ValueError("organization_patch_role_slot_payload_incomplete")
            if self.value.team_blueprint_ref is not None:
                raise ValueError("organization_patch_role_slot_team_blueprint_forbidden")
            if not (
                int(self.value.min_count or 0) <= int(self.value.default_count or 0) <= int(self.value.max_count)
                if self.value.max_count is not None
                else int(self.value.min_count or 0) <= int(self.value.default_count or 0)
            ):
                raise ValueError("organization_patch_role_slot_cardinality_invalid")
            if self.value.required and int(self.value.min_count or 0) < 1:
                raise ValueError("organization_patch_required_role_slot_minimum_invalid")
        elif required_slot_fields & self.value.model_fields_set:
            raise ValueError("organization_patch_unit_slot_fields_forbidden")
        elif self.node_kind == "team":
            VersionedDefinitionRef.parse(str(self.value.team_blueprint_ref or ""))
        elif self.value.team_blueprint_ref is not None:
            raise ValueError("organization_patch_structural_team_blueprint_forbidden")
        return self


class TopologyMigrationTarget(_Closed):
    organization_id: str = Field(min_length=1, max_length=191)
    unit_id: str = Field(min_length=1, max_length=191)
    team_id: str = Field(min_length=1, max_length=191)
    role_slot_id: str = Field(min_length=1, max_length=191)


class TopologyRemoveOperation(_Closed):
    op: Literal["remove"]
    node_id: str = Field(min_length=1, max_length=191)
    lifecycle_strategy: Literal["drain", "migrate", "archive"]
    migration_target: TopologyMigrationTarget | None = None

    @model_validator(mode="after")
    def validate_migration_target(self) -> "TopologyRemoveOperation":
        if self.lifecycle_strategy == "migrate" and self.migration_target is None:
            raise ValueError("organization_patch_migration_target_required")
        if self.lifecycle_strategy != "migrate" and self.migration_target is not None:
            raise ValueError("organization_patch_migration_target_unexpected")
        return self


class TopologyReparentOperation(_Closed):
    op: Literal["reparent"]
    node_id: str = Field(min_length=1, max_length=191)
    parent_id: str = Field(min_length=1, max_length=191)
    lifecycle_strategy: Literal["drain", "migrate"] | None = None


class TopologyConnectOperation(_Closed):
    op: Literal["connect"]
    namespace: Literal["organization"]
    edge_kind: _RELATION_KINDS
    source_id: str = Field(min_length=1, max_length=191)
    target_id: str = Field(min_length=1, max_length=191)
    relation_key: str | None = Field(default=None, max_length=191)
    dependency_policy: Literal["advisory", "declared", "gate"] = "declared"
    handoff_contract_ref: str | None = None
    escalation_policy: str = Field(default="hub", min_length=1, max_length=191)

    @model_validator(mode="after")
    def validate_connect(self) -> "TopologyConnectOperation":
        if self.source_id == self.target_id:
            raise ValueError("organization_patch_relation_self_reference")
        if self.handoff_contract_ref:
            VersionedDefinitionRef.parse(self.handoff_contract_ref)
        return self


class TopologyAssignOperation(_Closed):
    op: Literal["assign"]
    role_slot_id: str = Field(min_length=1, max_length=191)
    agent_id: str = Field(min_length=1, max_length=512)


TopologyPatchOperation = Annotated[
    TopologyAddOperation
    | TopologyRemoveOperation
    | TopologyReparentOperation
    | TopologyConnectOperation
    | TopologyAssignOperation,
    Field(discriminator="op"),
]


class OrganizationTopologyPatchDocument(_Closed):
    expected_revision: str = Field(min_length=1, max_length=191)
    operations: list[TopologyPatchOperation] = Field(min_length=1)


class OrganizationTopologyPatchPreview(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    tenant_id: str
    project_id: str
    organization_id: str
    principal_id: str
    expected_revision: str
    source_snapshot_hash: str
    patch_digest: str
    expires_at: str
    expires_at_epoch: float
    effective_limit_profile_ref: str
    effective_limit_profile_revision: int
    effective_limit_profile_hash: str
    effective_policy_hash: str
    budget_policy_hash: str
    operations: list[TopologyPatchOperation]
    planned_writes: list[str]
    diagnostics: list[dict[str, Any]]
    limits: dict[str, Any]
    applicable: bool

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"patch_digest"})


class OrganizationTopologyPatchApplyResult(_Closed):
    organization_id: str
    definition_revision: str
    snapshot_hash: str
    patch_digest: str
    applied_operations: int
    replayed: bool = False


class OrganizationTopologyPatchGrantResult(_Closed):
    grant_id: str
    grant_kind: Literal["topology_patch"] = "topology_patch"
    tenant_id: str
    project_id: str
    organization_id: str
    principal_id: str
    patch_digest: str
    policy_hash: str
    limit_hash: str
    expected_revision: str
    expires_at: float
    replayed: bool = False


class OrganizationTopologyPatchError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int = 409) -> None:
        self.reason_code = reason_code
        self.public_status = public_status
        super().__init__(reason_code)


@dataclass(slots=True)
class OrganizationPatchState:
    organization: Any
    snapshot: Any
    units: tuple[Any, ...]
    team_links: tuple[Any, ...]
    role_slots: tuple[Any, ...]
    assignments: tuple[Any, ...]
    relations: tuple[Any, ...]
    team_blueprints: dict[str, TeamBlueprintDefinition]
    team_blueprint_rows: dict[str, Any]
    role_template_refs: frozenset[str]
    workflow_steps: dict[str, int]
    agents: dict[str, Any]
    global_assignment_count_by_agent: dict[str, int]
    activity_by_unit: dict[str, dict[str, int]]
    effective_policy_hash: str
    budget_policy_hash: str | None
    handoff_definition_refs: frozenset[str]


class OrganizationPatchReadPort(Protocol):
    def load_state(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        agent_ids: set[str],
        session: Session | None = None,
        for_update: bool = False,
    ) -> OrganizationPatchState | None: ...


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


@dataclass(frozen=True, slots=True)
class _Evaluation:
    preview: OrganizationTopologyPatchPreview
    unit_activity: dict[str, dict[str, int]]


class OrganizationTopologyApplyService:
    def __init__(
        self,
        *,
        reader: OrganizationPatchReadPort,
        limit_profiles: OrganizationLimitProfilePort,
        uow_factory=OrganizationUnitOfWork,
        clock=time.time,
        preview_ttl_seconds: int = 300,
        grant_ttl_seconds: int = 120,
        fault_injector=None,
        catalog=None,
        assignment_eligibility: OrganizationAssignmentEligibilityService | None = None,
        active_work: SqlOrganizationActiveWorkService | None = None,
    ) -> None:
        self._reader = reader
        self._limit_profiles = limit_profiles
        self._uow_factory = uow_factory
        self._clock = clock
        self._ttl = max(30, min(int(preview_ttl_seconds), 1800))
        self._grant_ttl = max(30, min(int(grant_ttl_seconds), 300))
        self._fault_injector = fault_injector or (lambda _step: None)
        self._catalog = catalog
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()
        self._active_work = active_work or SqlOrganizationActiveWorkService()

    def preview(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        document: OrganizationTopologyPatchDocument,
    ) -> OrganizationTopologyPatchPreview:
        agent_ids = {row.agent_id for row in document.operations if isinstance(row, TopologyAssignOperation)}
        state = self._reader.load_state(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            agent_ids=agent_ids,
        )
        if state is None:
            raise OrganizationTopologyPatchError("organization_not_found", public_status=404)
        limits = self._resolve_limits(state)
        return self._evaluate(
            state=state,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            document=document,
            limits=limits,
            expires_at_epoch=self._clock() + self._ttl,
        ).preview

    def issue_grant(
        self,
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        expected_revision: str,
        expected_patch_digest: str,
        issue_idempotency_key: str,
        parent_admin_grant_id: str,
    ) -> OrganizationTopologyPatchGrantResult:
        """Issue one short-lived child grant from an unchanged preview."""

        self._validate_preview_envelope(
            preview=preview,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            expected_revision=expected_revision,
            expected_patch_digest=expected_patch_digest,
        )
        now = self._clock()
        if preview.expires_at_epoch < now:
            raise OrganizationTopologyPatchError(
                "organization_patch_preview_expired",
                public_status=412,
            )
        if not issue_idempotency_key or not parent_admin_grant_id:
            raise OrganizationTopologyPatchError(
                "organization_patch_grant_issue_binding_missing",
                public_status=400,
            )

        with self._uow_factory() as uow:
            membership = self._active_admin_membership(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
            )
            parent = uow.admin_grants.get_scoped(
                tenant_id,
                project_id,
                organization_id,
                parent_admin_grant_id,
                for_update=True,
            )
            if membership is None or parent is None:
                raise OrganizationTopologyPatchError(
                    "organization_patch_parent_authority_invalid",
                    public_status=403,
                )
            if (
                parent.principal_id != principal_id
                or parent.grant_kind != "organization_admin"
                or parent.revoked_at is not None
                or (parent.expires_at is not None and float(parent.expires_at) < now)
            ):
                raise OrganizationTopologyPatchError(
                    "organization_patch_parent_grant_invalid",
                    public_status=403,
                )

            existing = uow.topology_patch_grants.get_by_issue_idempotency_key(
                tenant_id,
                project_id,
                organization_id,
                principal_id,
                issue_idempotency_key,
                for_update=True,
            )
            if existing is not None:
                self._validate_grant_binding(
                    existing,
                    preview=preview,
                    principal_id=principal_id,
                )
                if existing.parent_admin_grant_id != parent_admin_grant_id:
                    raise OrganizationTopologyPatchError("organization_patch_grant_idempotency_conflict")
                return self._grant_result(existing, replayed=True)

            current = self._authoritative_preview(
                uow,
                preview=preview,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
            )
            self._require_unchanged_preview(preview, current)
            expires_at = min(float(preview.expires_at_epoch), now + self._grant_ttl)
            if expires_at <= now:
                raise OrganizationTopologyPatchError(
                    "organization_patch_preview_expired",
                    public_status=412,
                )
            grant = OrganizationTopologyPatchGrantDB(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
                parent_admin_grant_id=parent_admin_grant_id,
                patch_digest=preview.patch_digest,
                policy_hash=preview.effective_policy_hash,
                limit_hash=preview.effective_limit_profile_hash,
                expected_revision=preview.expected_revision,
                issue_idempotency_key=issue_idempotency_key,
                granted_by=principal_id,
                expires_at=expires_at,
            )
            uow.topology_patch_grants.add(grant)
            uow.audit_outbox.add(
                OrganizationAuditOutboxDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    event_key=f"organization-topology-patch-grant-issued:{grant.grant_id}",
                    event_kind="organization.topology_patch_grant_issued.v1",
                    payload_json={
                        "grant_id": grant.grant_id,
                        "principal_id": principal_id,
                        "patch_digest": preview.patch_digest,
                        "policy_hash": preview.effective_policy_hash,
                        "limit_hash": preview.effective_limit_profile_hash,
                        "expires_at": expires_at,
                    },
                )
            )
            return self._grant_result(grant)

    def apply(
        self,
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        expected_revision: str,
        expected_patch_digest: str,
        idempotency_key: str,
        topology_patch_grant_id: str,
    ) -> OrganizationTopologyPatchApplyResult:
        self._validate_preview_envelope(
            preview=preview,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            expected_revision=expected_revision,
            expected_patch_digest=expected_patch_digest,
        )
        if not idempotency_key or not topology_patch_grant_id:
            raise OrganizationTopologyPatchError("organization_patch_apply_binding_missing", public_status=400)

        request_digest = canonical_sha256(
            {
                "patch_digest": preview.patch_digest,
                "idempotency_key": idempotency_key,
                "topology_patch_grant_id": topology_patch_grant_id,
                "principal_id": principal_id,
            }
        )
        result: OrganizationTopologyPatchApplyResult | None = None
        with self._uow_factory() as uow:
            grant = uow.topology_patch_grants.get_scoped(
                tenant_id,
                project_id,
                organization_id,
                topology_patch_grant_id,
                for_update=True,
            )
            if grant is None:
                raise OrganizationTopologyPatchError(
                    "organization_patch_grant_invalid",
                    public_status=403,
                )
            self._validate_grant_binding(
                grant,
                preview=preview,
                principal_id=principal_id,
            )
            existing = uow.operations.get_by_idempotency_key(
                tenant_id,
                project_id,
                "topology_patch_apply",
                idempotency_key,
                for_update=True,
            )
            if existing is not None:
                if (
                    existing.request_digest != request_digest
                    or existing.plan_digest != preview.patch_digest
                    or grant.consumed_idempotency_key != idempotency_key
                    or grant.consumed_request_digest != request_digest
                    or grant.consumed_at is None
                ):
                    raise OrganizationTopologyPatchError("organization_patch_idempotency_conflict")
                if existing.status != "applied" or not existing.result_json:
                    raise OrganizationTopologyPatchError("organization_patch_apply_in_progress")
                result = OrganizationTopologyPatchApplyResult.model_validate({**existing.result_json, "replayed": True})
            else:
                membership = self._active_admin_membership(
                    uow,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    principal_id=principal_id,
                )
                if membership is None:
                    raise OrganizationTopologyPatchError(
                        "organization_patch_admin_authority_invalid",
                        public_status=403,
                    )
                now = self._clock()
                if (
                    grant.revoked_at is not None
                    or grant.consumed_at is not None
                    or float(grant.expires_at) < now
                    or preview.expires_at_epoch < now
                ):
                    raise OrganizationTopologyPatchError(
                        "organization_patch_grant_expired_or_consumed",
                        public_status=403,
                    )
                current = self._authoritative_preview(
                    uow,
                    preview=preview,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    principal_id=principal_id,
                )
                self._require_unchanged_preview(preview, current)
                state = current[1]
                operation = OrganizationOperationDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    operation_kind="topology_patch_apply",
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    plan_digest=preview.patch_digest,
                    expected_revision=preview.expected_revision,
                    status="pending",
                )
                uow.operations.add(operation)
                self._fault_injector("operation")
                self._stage_operations(
                    uow,
                    state,
                    preview.operations,
                    operation_key=preview.patch_digest,
                    principal_id=principal_id,
                )
                uow.flush()
                self._fault_injector("entities")
                snapshot_hash = self._stage_snapshot(uow, state, preview)
                state.organization.lock_version += 1
                state.organization.updated_at = self._clock()
                uow.instances.add(state.organization)
                result = OrganizationTopologyPatchApplyResult(
                    organization_id=organization_id,
                    definition_revision=state.organization.definition_revision,
                    snapshot_hash=snapshot_hash,
                    patch_digest=preview.patch_digest,
                    applied_operations=len(preview.operations),
                )
                uow.audit_outbox.add(
                    OrganizationAuditOutboxDB(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        organization_id=organization_id,
                        event_key=f"organization-topology-patched:{operation.operation_id}",
                        event_kind="organization.topology_patched.v1",
                        payload_json={
                            **result.model_dump(mode="json"),
                            "principal_id": principal_id,
                            "topology_patch_grant_id": topology_patch_grant_id,
                        },
                    )
                )
                operation.status = "applied"
                operation.result_ref = snapshot_hash
                operation.result_json = result.model_dump(mode="json")
                operation.applied_at = self._clock()
                uow.operations.add(operation)
                grant.consumed_at = self._clock()
                grant.revoked_at = grant.consumed_at
                grant.consumed_idempotency_key = idempotency_key
                grant.consumed_request_digest = request_digest
                uow.topology_patch_grants.add(grant)
                self._fault_injector("audit_outbox")
        if result is None:
            raise OrganizationTopologyPatchError("organization_patch_result_missing")
        return result

    @staticmethod
    def _grant_result(
        grant: OrganizationTopologyPatchGrantDB,
        *,
        replayed: bool = False,
    ) -> OrganizationTopologyPatchGrantResult:
        return OrganizationTopologyPatchGrantResult(
            grant_id=grant.grant_id,
            tenant_id=grant.tenant_id,
            project_id=grant.project_id,
            organization_id=grant.organization_id,
            principal_id=grant.principal_id,
            patch_digest=grant.patch_digest,
            policy_hash=grant.policy_hash,
            limit_hash=grant.limit_hash,
            expected_revision=grant.expected_revision,
            expires_at=grant.expires_at,
            replayed=replayed,
        )

    @staticmethod
    def _validate_grant_binding(
        grant: OrganizationTopologyPatchGrantDB,
        *,
        preview: OrganizationTopologyPatchPreview,
        principal_id: str,
    ) -> None:
        if (
            grant.tenant_id != preview.tenant_id
            or grant.project_id != preview.project_id
            or grant.organization_id != preview.organization_id
            or grant.principal_id != principal_id
            or grant.patch_digest != preview.patch_digest
            or grant.policy_hash != preview.effective_policy_hash
            or grant.limit_hash != preview.effective_limit_profile_hash
            or grant.expected_revision != preview.expected_revision
        ):
            raise OrganizationTopologyPatchError(
                "organization_patch_grant_binding_mismatch",
                public_status=403,
            )

    @staticmethod
    def _validate_preview_envelope(
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        expected_revision: str,
        expected_patch_digest: str,
    ) -> None:
        if canonical_sha256(preview.digest_payload()) != preview.patch_digest:
            raise OrganizationTopologyPatchError("organization_patch_preview_tampered")
        if preview.patch_digest != expected_patch_digest:
            raise OrganizationTopologyPatchError(
                "organization_patch_digest_header_mismatch",
                public_status=412,
            )
        if (
            preview.tenant_id != tenant_id
            or preview.project_id != project_id
            or preview.organization_id != organization_id
            or preview.principal_id != principal_id
        ):
            raise OrganizationTopologyPatchError(
                "organization_patch_scope_mismatch",
                public_status=403,
            )
        if preview.expected_revision != expected_revision:
            raise OrganizationTopologyPatchError(
                "organization_patch_if_match_mismatch",
                public_status=412,
            )
        if not preview.applicable:
            raise OrganizationTopologyPatchError(
                "organization_patch_preview_blocked",
                public_status=422,
            )

    def _active_admin_membership(
        self,
        uow,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
    ):
        return next(
            (
                row
                for row in uow.memberships.list_for_organization(
                    tenant_id,
                    project_id,
                    organization_id,
                )
                if row.principal_id == principal_id
                and row.membership_kind == "organization_admin"
                and (row.expires_at is None or float(row.expires_at) >= self._clock())
            ),
            None,
        )

    def _authoritative_preview(
        self,
        uow,
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
    ) -> tuple[OrganizationTopologyPatchPreview, OrganizationPatchState]:
        agent_ids = {row.agent_id for row in preview.operations if isinstance(row, TopologyAssignOperation)}
        state = self._reader.load_state(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            agent_ids=agent_ids,
            session=uow.session,
            for_update=True,
        )
        if state is None:
            raise OrganizationTopologyPatchError(
                "organization_not_found",
                public_status=404,
            )
        transaction_definitions = uow.definitions
        if self._catalog is not None:
            transaction_definitions = FileCatalogDefinitionRepositoryAdapter(
                transaction_definitions,
                self._catalog,
                uow.session,
            )
        limits = self._resolve_limits(
            state,
            port=SqlOrganizationLimitProfileAdapter(transaction_definitions),
        )
        current = self._evaluate(
            state=state,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            document=OrganizationTopologyPatchDocument(
                expected_revision=preview.expected_revision,
                operations=preview.operations,
            ),
            limits=limits,
            expires_at_epoch=preview.expires_at_epoch,
        ).preview
        return current, state

    @staticmethod
    def _require_unchanged_preview(
        preview: OrganizationTopologyPatchPreview,
        current: tuple[OrganizationTopologyPatchPreview, OrganizationPatchState],
    ) -> None:
        authoritative = current[0]
        if (
            authoritative.patch_digest != preview.patch_digest
            or authoritative.effective_policy_hash != preview.effective_policy_hash
            or authoritative.effective_limit_profile_hash != preview.effective_limit_profile_hash
        ):
            raise OrganizationTopologyPatchError(
                "organization_patch_preview_stale",
                public_status=412,
            )

    def _resolve_limits(
        self,
        state: OrganizationPatchState,
        *,
        port: OrganizationLimitProfilePort | None = None,
    ) -> OrganizationLimitProfile:
        reference = str(state.organization.effective_limit_profile_ref or "")
        if "@" not in reference:
            reference = f"{reference}@{state.organization.effective_limit_profile_revision}"
        return (port or self._limit_profiles).resolve_limit_profile(
            tenant_id=state.organization.tenant_id,
            project_id=state.organization.project_id,
            policy_ref=reference,
        )

    def _evaluate(  # noqa: C901 - one ordered interpreter preserves cross-operation draft state
        self, *, state, tenant_id, project_id, organization_id, principal_id, document, limits, expires_at_epoch
    ):
        diagnostics: list[OrganizationDiagnostic] = []
        planned_writes: list[str] = []

        def issue(path, code, message, *, severity="blocker", **details):
            diagnostics.append(
                OrganizationDiagnostic(
                    path=path,
                    reason_code=code,
                    human_message=message,
                    severity=severity,
                    details=details,
                )
            )

        if state.organization.definition_revision != document.expected_revision:
            issue("$.expected_revision", "ORGANIZATION_PATCH_REVISION_STALE", "If-Match definition revision is stale.")
        if state.snapshot is None:
            issue("$", "ORGANIZATION_PATCH_SNAPSHOT_MISSING", "Organization has no revision-bound topology snapshot.")
            source_snapshot_hash = "missing"
        else:
            source_snapshot_hash = state.snapshot.snapshot_hash
        if len(document.operations) > limits.max_patch_operations:
            issue(
                "$.operations",
                "ORGANIZATION_PATCH_OPERATION_LIMIT_EXCEEDED",
                "Patch exceeds the effective operation limit.",
            )
        if state.budget_policy_hash is None:
            issue("$", "ORGANIZATION_BUDGET_POLICY_MISSING", "Bound organization budget policy is unavailable.")

        units = {
            row.id: {
                "id": row.id,
                "key": row.unit_key,
                "kind": row.unit_kind,
                "parent_id": row.parent_unit_id,
                "lifecycle": row.lifecycle,
                "team_ref": (
                    f"{row.team_blueprint_key}@{row.team_blueprint_version}"
                    if row.team_blueprint_key and row.team_blueprint_version
                    else None
                ),
            }
            for row in state.units
            if row.lifecycle != "archived"
        }
        reserved_stable_keys = {row.unit_key for row in state.units}
        reserved_slot_keys = {(row.unit_id, row.slot_key) for row in state.role_slots}
        reserved_node_ids = {row.id for row in (*state.units, *state.role_slots)}
        slots = {
            row.id: {
                "id": row.id,
                "unit_id": row.unit_id,
                "key": row.slot_key,
                "required": row.required,
                "default_count": row.default_count,
                "max_count": row.max_count,
                "assignment_policy": dict(row.assignment_policy or {}),
                "sod": dict(row.separation_of_duties or {}),
                "lifecycle": row.lifecycle,
            }
            for row in state.role_slots
            if row.lifecycle != "archived"
        }
        assignments = [
            {"id": row.id, "slot_id": row.role_slot_id, "agent_id": row.agent_url}
            for row in state.assignments
            if row.lifecycle == "active"
        ]
        historical_assignments = {
            (row.role_slot_id, row.agent_url): row for row in state.assignments if row.lifecycle != "active"
        }
        relations = {
            row.id: {
                "id": row.id,
                "key": row.relation_key,
                "kind": row.kind,
                "source_id": row.source_unit_id,
                "target_id": row.target_unit_id,
                "dependency_policy": row.dependency_policy,
            }
            for row in state.relations
            if row.lifecycle == "active"
        }
        reserved_relation_keys = {row.relation_key for row in state.relations}
        reserved_relation_identities = {
            (row.kind, row.source_unit_id, row.target_unit_id) for row in state.relations if row.lifecycle == "active"
        }
        global_assignment_counts = dict(state.global_assignment_count_by_agent)

        for index, operation in enumerate(document.operations):
            path = f"$.operations[{index}]"
            if isinstance(operation, TopologyAddOperation):
                parent = units.get(operation.parent_id)
                if parent is None:
                    issue(
                        f"{path}.parent_id",
                        "ORGANIZATION_PATCH_PARENT_NOT_FOUND",
                        "Add parent is outside the active topology.",
                    )
                    continue
                planned_id = _planned_id(organization_id, operation.node_kind, operation.value.stable_key)
                if (
                    operation.value.stable_key in reserved_stable_keys
                    or planned_id in reserved_node_ids
                    or (
                        operation.node_kind == "role_slot"
                        and (operation.parent_id, str(operation.value.slot_key)) in reserved_slot_keys
                    )
                ):
                    issue(
                        f"{path}.value.stable_key",
                        "ORGANIZATION_PATCH_STABLE_KEY_CONFLICT",
                        "Stable key already exists in scope.",
                    )
                    continue
                if operation.node_kind == "role_slot":
                    if parent["kind"] != "team":
                        issue(
                            f"{path}.parent_id",
                            "ORGANIZATION_ROLE_SLOT_PARENT_INVALID",
                            "Role slots require a team parent.",
                        )
                        continue
                    if operation.value.role_template_ref not in state.role_template_refs:
                        issue(
                            f"{path}.value.role_template_ref",
                            "ROLE_TEMPLATE_NOT_FOUND",
                            "Role template revision is missing.",
                        )
                        continue
                    slots[planned_id] = {
                        "id": planned_id,
                        "unit_id": operation.parent_id,
                        "key": operation.value.slot_key,
                        "required": operation.value.required,
                        "default_count": operation.value.default_count,
                        "max_count": operation.value.max_count,
                        "assignment_policy": operation.value.assignment_policy.model_dump(mode="json"),
                        "sod": operation.value.separation_of_duties.model_dump(mode="json"),
                        "lifecycle": "active",
                    }
                    reserved_stable_keys.add(operation.value.stable_key)
                    reserved_slot_keys.add((operation.parent_id, str(operation.value.slot_key)))
                    reserved_node_ids.add(planned_id)
                    planned_writes.append(f"role_slot:create:{planned_id}")
                    continue
                if operation.node_kind not in PARENT_KIND_MATRIX.get(parent["kind"], set()):
                    issue(
                        f"{path}.parent_id",
                        "ORGANIZATION_PARENT_KIND_INVALID",
                        "Parent and child kinds are incompatible.",
                    )
                    continue
                team_ref = operation.value.team_blueprint_ref
                if operation.node_kind == "team" and team_ref not in state.team_blueprints:
                    issue(
                        f"{path}.value.team_blueprint_ref",
                        "TEAM_BLUEPRINT_NOT_FOUND",
                        "Team blueprint revision is missing.",
                    )
                    continue
                if operation.node_kind == "team":
                    missing_roles = sorted(
                        slot.role_template_ref
                        for slot in state.team_blueprints[team_ref].role_slots
                        if slot.role_template_ref not in state.role_template_refs
                    )
                    if missing_roles:
                        issue(
                            f"{path}.value.team_blueprint_ref",
                            "ROLE_TEMPLATE_NOT_FOUND",
                            "Team blueprint references unavailable role-template revisions.",
                            missing_role_template_refs=missing_roles,
                        )
                        continue
                units[planned_id] = {
                    "id": planned_id,
                    "key": operation.value.stable_key,
                    "kind": operation.node_kind,
                    "parent_id": operation.parent_id,
                    "lifecycle": "planned",
                    "team_ref": team_ref,
                }
                reserved_stable_keys.add(operation.value.stable_key)
                reserved_node_ids.add(planned_id)
                planned_writes.append(f"unit:create:{planned_id}")
                if operation.node_kind == "team":
                    planned_writes.extend((f"team:create:{planned_id}", f"team_link:create:{planned_id}"))
                    blueprint = state.team_blueprints[team_ref]
                    for slot in blueprint.role_slots:
                        slot_id = _planned_id(
                            organization_id, "role_slot", f"{operation.value.stable_key}:{slot.slot_id}"
                        )
                        slots[slot_id] = {
                            "id": slot_id,
                            "unit_id": planned_id,
                            "key": slot.slot_id,
                            "required": slot.required,
                            "default_count": slot.default_count,
                            "max_count": slot.max_count,
                            "assignment_policy": slot.assignment_policy.model_dump(mode="json"),
                            "sod": slot.separation_of_duties.model_dump(mode="json"),
                            "lifecycle": "active",
                        }
                        reserved_slot_keys.add((planned_id, slot.slot_id))
                        reserved_node_ids.add(slot_id)
                        planned_writes.append(f"role_slot:create:{slot_id}")
            elif isinstance(operation, TopologyRemoveOperation):
                unit = units.get(operation.node_id)
                if unit is not None:
                    subtree = _subtree_ids(units, operation.node_id)
                    activity = {
                        key: sum(int(state.activity_by_unit.get(unit_id, {}).get(key, 0)) for unit_id in subtree)
                        for key in {"tasks", "leases", "open_gates", "handoffs", "assignments"}
                    }
                    active = any(value > 0 for value in activity.values())
                    if active and operation.lifecycle_strategy == "archive":
                        issue(
                            path,
                            "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED",
                            "Active subtree work requires drain or an explicit migration target.",
                            activity=activity,
                        )
                        continue
                    if active:
                        planned_writes.append(f"active_work:{operation.lifecycle_strategy}:{operation.node_id}")
                    target_lifecycle = "archived"
                    planned_writes.append(f"unit:archive:{operation.node_id}")
                    for unit_id in subtree:
                        units[unit_id]["lifecycle"] = target_lifecycle
                    for relation_id, relation in list(relations.items()):
                        if target_lifecycle == "archived" and subtree & {
                            relation["source_id"],
                            relation["target_id"],
                        }:
                            relations.pop(relation_id)
                            planned_writes.append(f"relation:archive:{relation_id}")
                    continue
                slot = slots.get(operation.node_id)
                if slot is not None:
                    active_assignments = [row for row in assignments if row["slot_id"] == operation.node_id]
                    if active_assignments and operation.lifecycle_strategy == "archive":
                        issue(
                            path,
                            "ORGANIZATION_ROLE_SLOT_DRAIN_REQUIRED",
                            "Active assignments require drain or migrate before archive.",
                        )
                        continue
                    if active_assignments:
                        for assignment in active_assignments:
                            assignments.remove(assignment)
                            agent_id = assignment["agent_id"]
                            global_assignment_counts[agent_id] = max(
                                0,
                                int(global_assignment_counts.get(agent_id, 0)) - 1,
                            )
                            planned_writes.append(f"assignment:end:{assignment['id']}")
                    slots.pop(operation.node_id)
                    planned_writes.append(f"role_slot:{operation.lifecycle_strategy}:{operation.node_id}")
                    continue
                assignment = next((row for row in assignments if row["id"] == operation.node_id), None)
                if assignment:
                    assignments.remove(assignment)
                    agent_id = assignment["agent_id"]
                    global_assignment_counts[agent_id] = max(
                        0,
                        int(global_assignment_counts.get(agent_id, 0)) - 1,
                    )
                    planned_writes.append(f"assignment:end:{operation.node_id}")
                    continue
                relation = relations.pop(operation.node_id, None)
                if relation:
                    planned_writes.append(f"relation:archive:{operation.node_id}")
                    continue
                issue(
                    f"{path}.node_id",
                    "ORGANIZATION_PATCH_NODE_NOT_FOUND",
                    "Remove target is outside the mutable definition topology.",
                )
            elif isinstance(operation, TopologyReparentOperation):
                unit = units.get(operation.node_id)
                parent = units.get(operation.parent_id)
                if unit is None or parent is None:
                    issue(
                        path, "ORGANIZATION_PATCH_REPARENT_NODE_NOT_FOUND", "Reparent endpoints must be active units."
                    )
                    continue
                if unit["kind"] not in PARENT_KIND_MATRIX.get(parent["kind"], set()):
                    issue(path, "ORGANIZATION_PARENT_KIND_INVALID", "Reparent kinds are incompatible.")
                    continue
                subtree = _subtree_ids(units, operation.node_id)
                activity = _aggregate_subtree_activity(state.activity_by_unit, subtree)
                if any(int(value) > 0 for value in activity.values()) and operation.lifecycle_strategy is None:
                    issue(
                        path,
                        "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED",
                        "Active subtree work must be drained or migrated by the Hub before reparent can apply.",
                        activity=activity,
                    )
                    continue
                if any(int(value) > 0 for value in activity.values()):
                    planned_writes.append(f"active_work:{operation.lifecycle_strategy}:{operation.node_id}")
                unit["parent_id"] = operation.parent_id
                planned_writes.append(f"unit:reparent:{operation.node_id}:{operation.parent_id}")
            elif isinstance(operation, TopologyConnectOperation):
                source = units.get(operation.source_id)
                target = units.get(operation.target_id)
                if source is None or target is None:
                    issue(
                        path, "ORGANIZATION_RELATION_DANGLING", "Organization relation endpoints must be active units."
                    )
                    continue
                matrix = RELATION_ENDPOINT_KIND_MATRIX.get(operation.edge_kind)
                if matrix is None or source["kind"] not in matrix[0] or target["kind"] not in matrix[1]:
                    issue(
                        path, "ORGANIZATION_RELATION_ENDPOINT_KIND_INVALID", "Relation endpoint kinds are incompatible."
                    )
                    continue
                if (
                    operation.handoff_contract_ref
                    and operation.handoff_contract_ref not in state.handoff_definition_refs
                ):
                    issue(
                        f"{path}.handoff_contract_ref",
                        "ORGANIZATION_HANDOFF_DEFINITION_NOT_FOUND",
                        "Handoff definition revision is missing or inactive.",
                    )
                    continue
                relation_key = operation.relation_key or f"{operation.edge_kind}:{source['key']}:{target['key']}"
                relation_identity = (operation.edge_kind, operation.source_id, operation.target_id)
                if relation_key in reserved_relation_keys:
                    issue(path, "ORGANIZATION_RELATION_KEY_DUPLICATE", "Relation key already exists.")
                    continue
                if relation_identity in reserved_relation_identities:
                    issue(
                        path,
                        "ORGANIZATION_RELATION_IDENTITY_DUPLICATE",
                        "Relation kind and endpoints already identify an organization relation.",
                    )
                    continue
                relation_id = _planned_id(organization_id, "relation", relation_key)
                relations[relation_id] = {
                    "id": relation_id,
                    "key": relation_key,
                    "kind": operation.edge_kind,
                    "source_id": operation.source_id,
                    "target_id": operation.target_id,
                    "dependency_policy": operation.dependency_policy,
                }
                reserved_relation_keys.add(relation_key)
                reserved_relation_identities.add(relation_identity)
                planned_writes.append(f"relation:create:{relation_id}")
            else:
                slot = slots.get(operation.role_slot_id)
                agent = state.agents.get(operation.agent_id)
                if slot is None:
                    issue(path, "ORGANIZATION_ROLE_SLOT_NOT_FOUND", "Assignment role slot is missing.")
                    continue
                policy = AssignmentPolicyDefinition.model_validate(slot["assignment_policy"])
                historical = historical_assignments.get((operation.role_slot_id, operation.agent_id))
                capacity_used = int(global_assignment_counts.get(operation.agent_id, 0))
                if historical is not None and historical.lifecycle == "proposed":
                    # A proposed row already reserves one unit of global
                    # capacity; activating it does not consume another.
                    capacity_used = max(0, capacity_used - 1)
                eligibility = self._assignment_eligibility.evaluate(
                    agent=agent,
                    required_capabilities=set(policy.required_capabilities),
                    forbidden_capabilities=set(policy.forbidden_capabilities),
                    capacity_used=capacity_used,
                    principal_kind_allowed="agent" in policy.principal_kinds,
                    write_access_required=policy.write_access_required,
                )
                directory_reasons = {
                    "agent_not_registered",
                    "agent_registration_unvalidated",
                    "agent_not_online",
                }
                if directory_reasons & set(eligibility.reasons):
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_AGENT_INELIGIBLE",
                        "Agent is missing, offline, or not registration-validated.",
                        reasons=list(eligibility.reasons),
                    )
                    continue
                capability_reasons = {
                    reason
                    for reason in eligibility.reasons
                    if reason.startswith(("missing_capability:", "forbidden_capability:"))
                    or reason == "assignment_principal_kind_not_allowed"
                }
                if capability_reasons:
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_CAPABILITY_MISMATCH",
                        "Agent capabilities violate the role-slot assignment policy.",
                        reasons=sorted(capability_reasons),
                    )
                    continue
                if "write_access_required" in eligibility.reasons:
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_WRITE_ACCESS_REQUIRED",
                        "Role slot requires validated write access.",
                    )
                    continue
                capacity_reasons = {
                    reason
                    for reason in eligibility.reasons
                    if reason in {"agent_capacity_exhausted", "agent_capacity_invalid"}
                }
                if capacity_reasons:
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_AGENT_CAPACITY_EXHAUSTED",
                        "Agent-global organization assignment capacity is unavailable.",
                        reasons=sorted(capacity_reasons),
                        capacity_used=eligibility.capacity_used,
                        capacity_limit=eligibility.capacity_limit,
                    )
                    continue
                existing_for_slot = [row for row in assignments if row["slot_id"] == operation.role_slot_id]
                if any(row["agent_id"] == operation.agent_id for row in existing_for_slot):
                    issue(path, "ORGANIZATION_ASSIGNMENT_DUPLICATE", "Agent is already assigned to this role slot.")
                    continue
                if slot["max_count"] is not None and len(existing_for_slot) >= int(slot["max_count"]):
                    issue(path, "ORGANIZATION_ROLE_SLOT_CAPACITY_EXCEEDED", "Role-slot assignment maximum is reached.")
                    continue
                separation = evaluate_organization_slot_separation(
                    target=OrganizationSlotSeparationPolicy(
                        slot_id=slot["id"],
                        slot_key=slot["key"],
                        definition=SeparationOfDutiesDefinition.model_validate(slot["sod"]),
                    ),
                    peers=(
                        OrganizationSlotSeparationPolicy(
                            slot_id=row["id"],
                            slot_key=row["key"],
                            definition=SeparationOfDutiesDefinition.model_validate(row["sod"]),
                        )
                        for row in slots.values()
                        if row["unit_id"] == slot["unit_id"]
                    ),
                    assigned_slot_ids=(row["slot_id"] for row in assignments if row["agent_id"] == operation.agent_id),
                    agent_capabilities=eligibility.capabilities,
                )
                if separation.has_conflict and separation.enforcement == "strict":
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_SOD_CONFLICT",
                        "Assignment violates strict separation of duties.",
                        conflicting_role_slot_ids=list(separation.conflicting_slot_ids),
                        external_duties=list(separation.external_duties),
                    )
                    continue
                if separation.has_conflict and separation.enforcement == "warn":
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_SOD_WARNING",
                        "Assignment has a declared separation-of-duties conflict.",
                        severity="warning",
                        conflicting_role_slot_ids=list(separation.conflicting_slot_ids),
                        external_duties=list(separation.external_duties),
                    )
                assignment_id = (
                    historical.id
                    if historical is not None
                    else _planned_id(
                        organization_id,
                        "assignment",
                        f"{operation.role_slot_id}:{operation.agent_id}",
                    )
                )
                assignments.append(
                    {"id": assignment_id, "slot_id": operation.role_slot_id, "agent_id": operation.agent_id}
                )
                if historical is None or historical.lifecycle != "proposed":
                    global_assignment_counts[operation.agent_id] = (
                        int(global_assignment_counts.get(operation.agent_id, 0)) + 1
                    )
                action = "reactivate" if historical is not None else "create"
                planned_writes.append(f"assignment:{action}:{assignment_id}")

        if _parent_cycle(units):
            issue("$.operations", "ORGANIZATION_HIERARCHY_CYCLE", "Patch would introduce a hierarchy cycle.")
        dependency_relations = [row for row in relations.values() if row["dependency_policy"] in {"declared", "gate"}]
        if _relation_cycle(dependency_relations):
            issue("$.operations", "ORGANIZATION_DEPENDENCY_CYCLE", "Patch would introduce a dependency cycle.")

        active_units = [row for row in units.values() if row["lifecycle"] != "archived"]
        active_team_refs = [row["team_ref"] for row in active_units if row["kind"] == "team"]
        workflow_steps = sum(
            state.workflow_steps.get(state.team_blueprints[ref].workflow_ref, 0)
            for ref in active_team_refs
            if ref in state.team_blueprints
        )
        counts = {
            "team": len(active_team_refs),
            "unit": len(active_units),
            "role_slot": len(slots),
            "assignment": len(assignments),
            "relation": len(relations),
            "workflow_step": workflow_steps,
        }
        checks = (
            (counts["team"], limits.max_team_instances_per_organization, "ORGANIZATION_TEAM_LIMIT_EXCEEDED"),
            (counts["unit"], limits.max_units_per_organization, "ORGANIZATION_UNIT_LIMIT_EXCEEDED"),
            (counts["role_slot"], limits.max_role_slots_per_organization, "ORGANIZATION_ROLE_SLOT_LIMIT_EXCEEDED"),
            (counts["assignment"], limits.max_assignments_per_organization, "ORGANIZATION_ASSIGNMENT_LIMIT_EXCEEDED"),
            (counts["relation"], limits.max_relations_per_organization, "ORGANIZATION_RELATION_LIMIT_EXCEEDED"),
            (
                counts["workflow_step"],
                limits.max_workflow_steps_per_organization,
                "ORGANIZATION_WORKFLOW_STEP_LIMIT_EXCEEDED",
            ),
        )
        for actual, maximum, reason_code in checks:
            if actual > maximum:
                issue(
                    "$.operations",
                    reason_code,
                    "Patch exceeds an effective organization limit.",
                    actual=actual,
                    maximum=maximum,
                )

        expires_at = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        diagnostic_payload = [
            {
                "severity": item.severity,
                "reason_code": item.reason_code,
                "message": item.human_message,
                **({"node_ids": item.details.get("node_ids")} if item.details.get("node_ids") else {}),
                **({"activity": item.details.get("activity")} if item.details.get("activity") else {}),
            }
            for item in diagnostics
        ]
        limit_ref = str(state.organization.effective_limit_profile_ref or "")
        if "@" not in limit_ref:
            limit_ref = f"{limit_ref}@{state.organization.effective_limit_profile_revision}"
        payload = {
            "schema_version": "1.0",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "organization_id": organization_id,
            "principal_id": principal_id,
            "expected_revision": document.expected_revision,
            "source_snapshot_hash": source_snapshot_hash,
            "expires_at": expires_at,
            "expires_at_epoch": expires_at_epoch,
            "effective_limit_profile_ref": limit_ref,
            "effective_limit_profile_revision": limits.revision,
            "effective_limit_profile_hash": limits.content_hash(),
            "effective_policy_hash": state.effective_policy_hash,
            "budget_policy_hash": state.budget_policy_hash or "missing",
            # Preserve the request's field-presence contract.  Serializing
            # defaulted ``None`` role-slot fields into structural add
            # operations would make a valid document fail its second model
            # validation when the preview is constructed.
            "operations": [row.model_dump(mode="json", exclude_unset=True) for row in document.operations],
            "planned_writes": sorted(set(planned_writes)),
            "diagnostics": diagnostic_payload,
            "limits": _angular_limits(limits),
            "applicable": not any(row.severity == "blocker" for row in diagnostics),
        }
        preview = OrganizationTopologyPatchPreview(
            **payload,
            patch_digest="0" * 64,
        )
        preview = preview.model_copy(update={"patch_digest": canonical_sha256(preview.digest_payload())})
        return _Evaluation(preview=preview, unit_activity=state.activity_by_unit)

    def _stage_operations(self, uow, state, operations, *, operation_key: str, principal_id: str):
        units = {row.id: row for row in state.units}
        slots = {row.id: row for row in state.role_slots}
        assignments = {row.id: row for row in state.assignments}
        relations = {row.id: row for row in state.relations}
        links_by_unit = {row.unit_id: row for row in state.team_links}
        for index, operation in enumerate(operations):
            if isinstance(operation, TopologyAddOperation):
                planned_id = _planned_id(
                    state.organization.organization_id, operation.node_kind, operation.value.stable_key
                )
                if operation.node_kind == "role_slot":
                    role_ref = VersionedDefinitionRef.parse(str(operation.value.role_template_ref))
                    row = OrganizationRoleSlotDB(
                        id=planned_id,
                        tenant_id=state.organization.tenant_id,
                        project_id=state.organization.project_id,
                        organization_id=state.organization.organization_id,
                        unit_id=operation.parent_id,
                        slot_key=str(operation.value.slot_key),
                        role_template_key=role_ref.key,
                        role_template_version=role_ref.version,
                        required=bool(operation.value.required),
                        min_count=int(operation.value.min_count or 0),
                        default_count=int(operation.value.default_count or 0),
                        max_count=operation.value.max_count,
                        assignment_policy=operation.value.assignment_policy.model_dump(mode="json"),
                        separation_of_duties=operation.value.separation_of_duties.model_dump(mode="json"),
                        overlays=operation.value.overlays,
                    )
                    uow.role_slots.add(row)
                    slots[row.id] = row
                else:
                    team_ref = (
                        VersionedDefinitionRef.parse(operation.value.team_blueprint_ref)
                        if operation.value.team_blueprint_ref
                        else None
                    )
                    row = OrganizationUnitDB(
                        id=planned_id,
                        tenant_id=state.organization.tenant_id,
                        project_id=state.organization.project_id,
                        organization_id=state.organization.organization_id,
                        unit_key=operation.value.stable_key,
                        name=operation.value.name,
                        unit_kind=operation.node_kind,
                        parent_unit_id=operation.parent_id,
                        team_blueprint_key=team_ref.key if team_ref else None,
                        team_blueprint_version=team_ref.version if team_ref else None,
                        lifecycle="planned",
                    )
                    uow.units.add(row)
                    units[row.id] = row
                    if team_ref:
                        portable_ref = team_ref.portable_ref()
                        blueprint_row = state.team_blueprint_rows[portable_ref]
                        team_id = _planned_id(state.organization.organization_id, "team", operation.value.stable_key)
                        uow.teams.add(
                            TeamDB(
                                id=team_id,
                                name=f"{state.organization.name} / {operation.value.name}",
                                description=f"Organization-managed team {operation.value.stable_key}",
                                blueprint_id=blueprint_row.legacy_blueprint_id,
                                is_active=False,
                                blueprint_snapshot={
                                    "definition_ref": portable_ref,
                                    "definition_hash": blueprint_row.content_hash,
                                },
                            )
                        )
                        link = OrganizationTeamLinkDB(
                            tenant_id=state.organization.tenant_id,
                            project_id=state.organization.project_id,
                            organization_id=state.organization.organization_id,
                            unit_id=row.id,
                            team_id=team_id,
                            lifecycle="planned",
                        )
                        uow.team_links.add(link)
                        links_by_unit[row.id] = link
                        for definition in state.team_blueprints[portable_ref].role_slots:
                            role_ref = VersionedDefinitionRef.parse(definition.role_template_ref)
                            slot_id = _planned_id(
                                state.organization.organization_id,
                                "role_slot",
                                f"{operation.value.stable_key}:{definition.slot_id}",
                            )
                            slot_row = OrganizationRoleSlotDB(
                                id=slot_id,
                                tenant_id=state.organization.tenant_id,
                                project_id=state.organization.project_id,
                                organization_id=state.organization.organization_id,
                                unit_id=row.id,
                                slot_key=definition.slot_id,
                                role_template_key=role_ref.key,
                                role_template_version=role_ref.version,
                                required=definition.required,
                                min_count=definition.min_count,
                                default_count=definition.default_count,
                                max_count=definition.max_count,
                                assignment_policy=definition.assignment_policy.model_dump(mode="json"),
                                separation_of_duties=definition.separation_of_duties.model_dump(mode="json"),
                                overlays=definition.overlays,
                            )
                            uow.role_slots.add(slot_row)
                            slots[slot_row.id] = slot_row
            elif isinstance(operation, TopologyRemoveOperation):
                if operation.node_id in units:
                    unit_view = {
                        row_id: {"parent_id": row.parent_unit_id}
                        for row_id, row in units.items()
                        if row.lifecycle != "archived"
                    }
                    subtree = _subtree_ids(unit_view, operation.node_id)
                    active = any(
                        int(value) > 0
                        for unit_id in subtree
                        for value in state.activity_by_unit.get(unit_id, {}).values()
                    )
                    if active:
                        if operation.lifecycle_strategy == "archive":
                            raise OrganizationTopologyPatchError("organization_active_work_strategy_required")
                        try:
                            self._active_work.execute(
                                session=uow.session,
                                tenant_id=state.organization.tenant_id,
                                project_id=state.organization.project_id,
                                organization_id=state.organization.organization_id,
                                strategy=operation.lifecycle_strategy,
                                operation_key=f"{operation_key}:{index}",
                                principal_id=principal_id,
                                migration_target=(
                                    operation.migration_target.model_dump(mode="json")
                                    if operation.migration_target is not None
                                    else None
                                ),
                                unit_ids=tuple(sorted(subtree)),
                                include_queued=True,
                                now=self._clock(),
                            )
                        except OrganizationActiveWorkError as exc:
                            raise OrganizationTopologyPatchError(exc.reason_code, public_status=422) from exc
                    for assignment in assignments.values():
                        if (
                            assignment.lifecycle == "active"
                            and slots.get(assignment.role_slot_id) is not None
                            and slots[assignment.role_slot_id].unit_id in subtree
                        ):
                            assignment.lifecycle = "ended"
                            assignment.ended_at = self._clock()
                            uow.assignments.add(assignment)
                    lifecycle = "archived"
                    for unit_id in subtree:
                        row = units[unit_id]
                        row.lifecycle = lifecycle
                        uow.units.add(row)
                        if row.id in links_by_unit:
                            links_by_unit[row.id].lifecycle = lifecycle
                            uow.team_links.add(links_by_unit[row.id])
                    for relation in relations.values():
                        if lifecycle == "archived" and subtree & {
                            relation.source_unit_id,
                            relation.target_unit_id,
                        }:
                            relation.lifecycle = "archived"
                            uow.relations.add(relation)
                elif operation.node_id in slots:
                    has_active = any(
                        assignment.role_slot_id == operation.node_id and assignment.lifecycle == "active"
                        for assignment in assignments.values()
                    )
                    if has_active and operation.lifecycle_strategy == "archive":
                        raise OrganizationTopologyPatchError("organization_role_slot_drain_required")
                    if has_active:
                        for assignment in assignments.values():
                            if assignment.role_slot_id == operation.node_id and assignment.lifecycle == "active":
                                assignment.lifecycle = "ended"
                                assignment.ended_at = self._clock()
                                uow.assignments.add(assignment)
                    slots[operation.node_id].lifecycle = "archived"
                    uow.role_slots.add(slots[operation.node_id])
                elif operation.node_id in assignments:
                    assignments[operation.node_id].lifecycle = "ended"
                    assignments[operation.node_id].ended_at = self._clock()
                    uow.assignments.add(assignments[operation.node_id])
                elif operation.node_id in relations:
                    relations[operation.node_id].lifecycle = "archived"
                    uow.relations.add(relations[operation.node_id])
            elif isinstance(operation, TopologyReparentOperation):
                unit_view = {
                    row_id: {"parent_id": row.parent_unit_id}
                    for row_id, row in units.items()
                    if row.lifecycle != "archived"
                }
                subtree = _subtree_ids(unit_view, operation.node_id)
                active = any(
                    int(value) > 0 for unit_id in subtree for value in state.activity_by_unit.get(unit_id, {}).values()
                )
                if active:
                    if operation.lifecycle_strategy is None:
                        raise OrganizationTopologyPatchError("organization_active_work_strategy_required")
                    try:
                        self._active_work.execute(
                            session=uow.session,
                            tenant_id=state.organization.tenant_id,
                            project_id=state.organization.project_id,
                            organization_id=state.organization.organization_id,
                            strategy=operation.lifecycle_strategy,
                            operation_key=f"{operation_key}:{index}",
                            principal_id=principal_id,
                            unit_ids=tuple(sorted(subtree)),
                            allow_in_place_migration=operation.lifecycle_strategy == "migrate",
                            include_queued=True,
                            now=self._clock(),
                        )
                    except OrganizationActiveWorkError as exc:
                        raise OrganizationTopologyPatchError(exc.reason_code, public_status=422) from exc
                units[operation.node_id].parent_unit_id = operation.parent_id
                units[operation.node_id].updated_at = self._clock()
                uow.units.add(units[operation.node_id])
            elif isinstance(operation, TopologyConnectOperation):
                relation_key = operation.relation_key or (
                    f"{operation.edge_kind}:{units[operation.source_id].unit_key}:{units[operation.target_id].unit_key}"
                )
                handoff_ref = (
                    VersionedDefinitionRef.parse(operation.handoff_contract_ref)
                    if operation.handoff_contract_ref
                    else None
                )
                row = OrganizationRelationDB(
                    id=_planned_id(state.organization.organization_id, "relation", relation_key),
                    tenant_id=state.organization.tenant_id,
                    project_id=state.organization.project_id,
                    organization_id=state.organization.organization_id,
                    relation_key=relation_key,
                    namespace="organization",
                    kind=operation.edge_kind,
                    source_unit_id=operation.source_id,
                    target_unit_id=operation.target_id,
                    handoff_definition_key=handoff_ref.key if handoff_ref else None,
                    handoff_definition_version=handoff_ref.version if handoff_ref else None,
                    dependency_policy=operation.dependency_policy,
                    escalation_policy=operation.escalation_policy,
                )
                uow.relations.add(row)
                relations[row.id] = row
            else:
                row = next(
                    (
                        value
                        for value in assignments.values()
                        if value.role_slot_id == operation.role_slot_id and value.agent_url == operation.agent_id
                    ),
                    None,
                )
                if row is None:
                    row = OrganizationRoleAssignmentDB(
                        id=_planned_id(
                            state.organization.organization_id,
                            "assignment",
                            f"{operation.role_slot_id}:{operation.agent_id}",
                        ),
                        tenant_id=state.organization.tenant_id,
                        project_id=state.organization.project_id,
                        organization_id=state.organization.organization_id,
                        role_slot_id=operation.role_slot_id,
                        agent_url=operation.agent_id,
                    )
                # ``agent_id`` has already been resolved against the Hub's
                # validated Agent registry during preview and revalidation.
                # Persist that canonical identity so later routing and
                # separation-of-duties decisions never infer a principal from
                # a display label or request-body claim.
                metadata = dict(row.assignment_metadata or {})
                metadata.update(
                    {
                        "principal_kind": "registered_worker",
                        "principal_id": operation.agent_id,
                    }
                )
                row.assignment_metadata = metadata
                row.lifecycle = "active"
                row.ended_at = None
                row.assigned_at = self._clock()
                uow.assignments.add(row)
                assignments[row.id] = row
            self._fault_injector(f"operation:{index}")

    def _stage_snapshot(self, uow, state, preview):
        units = uow.units.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        links = uow.team_links.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        slots = uow.role_slots.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        assignments = uow.assignments.list_for_organization(
            preview.tenant_id, preview.project_id, preview.organization_id
        )
        relations = uow.relations.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        unit_by_id = {row.id: row for row in units}
        link_by_unit = {row.unit_id: row for row in links}
        payload = {
            "organization_id": preview.organization_id,
            "definition_revision": state.organization.definition_revision,
            "parent_snapshot_hash": preview.source_snapshot_hash,
            "local_patch_digest": preview.patch_digest,
            "units": [
                {
                    "id": row.id,
                    "unit_key": row.unit_key,
                    "name": row.name,
                    "unit_kind": row.unit_kind,
                    "parent_unit_key": unit_by_id[row.parent_unit_id].unit_key
                    if row.parent_unit_id in unit_by_id
                    else None,
                    "team_id": link_by_unit[row.id].team_id if row.id in link_by_unit else None,
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(units, key=lambda value: value.unit_key)
            ],
            "role_slots": [
                {
                    "id": row.id,
                    "unit_id": row.unit_id,
                    "slot_key": row.slot_key,
                    "role_template_ref": f"{row.role_template_key}@{row.role_template_version}",
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(slots, key=lambda value: (value.unit_id, value.slot_key))
            ],
            "assignments": [
                {
                    "id": row.id,
                    "role_slot_id": row.role_slot_id,
                    "agent_url": row.agent_url,
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(assignments, key=lambda value: value.id)
            ],
            "relations": [
                {
                    "id": row.id,
                    "relation_key": row.relation_key,
                    "kind": row.kind,
                    "source_unit_id": row.source_unit_id,
                    "target_unit_id": row.target_unit_id,
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(relations, key=lambda value: value.relation_key)
            ],
        }
        if state.snapshot and (state.snapshot.snapshot_json or {}).get("compiled_plan"):
            payload["compiled_plan"] = state.snapshot.snapshot_json["compiled_plan"]
        snapshot_hash = canonical_sha256(payload)
        revision = int(state.snapshot.revision if state.snapshot else 0) + 1
        uow.snapshots.add(
            OrganizationTopologySnapshotDB(
                tenant_id=preview.tenant_id,
                project_id=preview.project_id,
                organization_id=preview.organization_id,
                revision=revision,
                definition_revision=state.organization.definition_revision,
                snapshot_hash=snapshot_hash,
                snapshot_json=payload,
            )
        )
        return snapshot_hash


def _planned_id(organization_id: str, kind: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ananta:organization-patch:{organization_id}:{kind}:{key}"))


def _subtree_ids(units: dict[str, dict[str, Any]], root_id: str) -> set[str]:
    result = {root_id}
    changed = True
    while changed:
        changed = False
        for unit_id, unit in units.items():
            if unit_id not in result and unit.get("parent_id") in result:
                result.add(unit_id)
                changed = True
    return result


def _aggregate_subtree_activity(
    activity_by_unit: dict[str, dict[str, int]],
    subtree: set[str],
) -> dict[str, int]:
    activity_keys = {key for unit_id in subtree for key in activity_by_unit.get(unit_id, {})}
    return {
        key: sum(int(activity_by_unit.get(unit_id, {}).get(key, 0)) for unit_id in subtree)
        for key in sorted(activity_keys)
    }


def _parent_cycle(units: dict[str, dict[str, Any]]) -> bool:
    for start in units:
        seen: set[str] = set()
        current = start
        while current in units:
            if current in seen:
                return True
            seen.add(current)
            current = str(units[current].get("parent_id") or "")
    return False


def _relation_cycle(relations: list[dict[str, Any]]) -> bool:
    graph: dict[str, set[str]] = {}
    for row in relations:
        graph.setdefault(row["source_id"], set()).add(row["target_id"])
        graph.setdefault(row["target_id"], set())
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(target) for target in graph.get(node, set())):
            return True
        state[node] = 2
        return False

    return any(visit(node) for node in sorted(graph) if not state.get(node))


def _angular_limits(limits: OrganizationLimitProfile) -> dict[str, Any]:
    return {
        "revision": str(limits.revision),
        "policy_hash": limits.content_hash(),
        "max_teams": limits.max_team_instances_per_organization,
        "max_units": limits.max_units_per_organization,
        "max_role_slots": limits.max_role_slots_per_organization,
        "max_assignments": limits.max_assignments_per_organization,
        "max_relations": limits.max_relations_per_organization,
        "max_patch_operations": limits.max_patch_operations,
        "max_page_size": limits.topology_max_page_size,
        "max_depth": limits.topology_max_depth,
        "max_render_nodes": limits.canvas_render_node_limit,
        "max_render_edges": limits.canvas_render_edge_limit,
    }


__all__ = [
    "OrganizationPatchReadPort",
    "OrganizationTopologyApplyService",
    "OrganizationTopologyPatchApplyResult",
    "OrganizationTopologyPatchDocument",
    "OrganizationTopologyPatchError",
    "OrganizationTopologyPatchGrantResult",
    "OrganizationTopologyPatchPreview",
    "SqlOrganizationPatchReadAdapter",
    "TopologyAddOperation",
    "TopologyAssignOperation",
    "TopologyConnectOperation",
    "TopologyPatchOperation",
    "TopologyRemoveOperation",
    "TopologyReparentOperation",
]
