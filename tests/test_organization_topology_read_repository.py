from __future__ import annotations

from agent.db_models.blueprints import ArtifactDB, ArtifactVersionDB
from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationInstanceDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.tasks import TaskDB
from agent.repositories.organizations.topology import (
    SqlOrganizationTopologyReadRepository,
)


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def first(self):
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class EntityRowsSession:
    """Small query-shape stub; repository filtering remains production code."""

    def __init__(self, rows_by_entity):
        self._rows_by_entity = rows_by_entity
        self.statements = []

    def exec(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        return _Rows(self._rows_by_entity.get(entity, ()))


def _organization() -> OrganizationInstanceDB:
    return OrganizationInstanceDB(
        organization_id="organization-a",
        tenant_id="tenant-a",
        project_id="project-a",
        name="Organization A",
        definition_key="enterprise-scrum",
        definition_version=1,
        definition_revision="d" * 64,
        effective_limit_profile_ref="organization-limits@1",
        effective_limit_profile_revision=1,
        effective_limit_profile_hash="l" * 64,
        composition_mode="standard",
        plan_digest="p" * 64,
        idempotency_key="organization-create-a",
    )


def _units() -> list[OrganizationUnitDB]:
    return [
        OrganizationUnitDB(
            id="coordination-a",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_key="coordination-a",
            name="Coordination A",
            unit_kind="coordination_unit",
        ),
        OrganizationUnitDB(
            id="team-a",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_key="team-a",
            name="Team A",
            unit_kind="team",
            parent_unit_id="coordination-a",
        ),
    ]


def _repository() -> SqlOrganizationTopologyReadRepository:
    slot = OrganizationRoleSlotDB(
        id="slot-a",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        unit_id="team-a",
        slot_key="developer",
        role_template_key="developer",
        role_template_version=1,
    )
    assignment = OrganizationRoleAssignmentDB(
        id="assignment-a",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        role_slot_id="slot-a",
        agent_url="worker-a",
    )
    snapshot = OrganizationTopologySnapshotDB(
        id="snapshot-a",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        revision=1,
        definition_revision="d" * 64,
        snapshot_hash="s" * 64,
    )
    return SqlOrganizationTopologyReadRepository(
        EntityRowsSession(
            {
                OrganizationInstanceDB: [_organization()],
                OrganizationUnitDB: _units(),
                OrganizationRoleSlotDB: [slot],
                OrganizationRoleAssignmentDB: [assignment],
                OrganizationTopologySnapshotDB: [snapshot],
            }
        )
    )


def _snapshot_for_kinds(*kinds: str):
    return _repository().load_topology_snapshot(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        include_runtime_overlay=False,
        cursor=None,
        limit=10,
        max_depth=4,
        filters={"kinds": list(kinds)},
    )


def test_repository_respects_each_explicit_node_kind_server_side() -> None:
    organization = _snapshot_for_kinds("organization")
    coordination = _snapshot_for_kinds("coordination_unit")
    role_slot = _snapshot_for_kinds("role_slot")
    assignment = _snapshot_for_kinds("assignment")

    assert organization["units"] == []
    assert organization["role_slots"] == []
    assert organization["assignments"] == []
    assert [unit["unit_kind"] for unit in coordination["units"]] == ["coordination_unit"]
    assert coordination["role_slots"] == []
    assert coordination["assignments"] == []
    assert role_slot["units"] == []
    assert [slot["id"] for slot in role_slot["role_slots"]] == ["slot-a"]
    assert role_slot["assignments"] == []
    assert assignment["units"] == []
    assert assignment["role_slots"] == []
    assert [row["id"] for row in assignment["assignments"]] == ["assignment-a"]


def test_repository_keeps_unit_cursor_budget_when_filtered_children_are_loaded() -> None:
    first = _repository().load_topology_snapshot(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        include_runtime_overlay=False,
        cursor=None,
        limit=1,
        max_depth=4,
        filters={"kinds": ["role_slot"]},
    )

    assert [slot["id"] for slot in first["role_slots"]] == ["slot-a"]
    assert first["next_cursor"] is None


def test_runtime_nodes_are_revision_bound_and_derived_from_scoped_metadata() -> None:
    units = [
        *_units(),
        OrganizationUnitDB(
            id="team-b",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_key="team-b",
            name="Team B",
            unit_kind="team",
            parent_unit_id="coordination-a",
        ),
    ]
    links = [
        OrganizationTeamLinkDB(
            id="link-a",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_id="team-a",
            team_id="runtime-team-a",
            lifecycle="active",
        ),
        OrganizationTeamLinkDB(
            id="link-b",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_id="team-b",
            team_id="runtime-team-b",
            lifecycle="active",
        ),
    ]
    slot = OrganizationRoleSlotDB(
        id="slot-b",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        unit_id="team-b",
        slot_key="developer",
        role_template_key="developer",
        role_template_version=1,
        max_count=4,
    )
    assignment = OrganizationRoleAssignmentDB(
        id="assignment-b",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        role_slot_id="slot-b",
        agent_url="worker-b",
        lifecycle="active",
    )
    tasks = [
        TaskDB(
            id="task-source",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_id="team-a",
            team_id="runtime-team-a",
            status="running",
            description="must-not-be-selected",
            last_output="must-not-be-selected",
        ),
        TaskDB(
            id="task-target",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            unit_id="team-b",
            team_id="runtime-team-b",
            role_slot_id="slot-b",
            assigned_agent_url="worker-b",
            status="blocked",
            status_reason_code="WAITING_FOR_REVIEW",
        ),
    ]
    dependency = CrossTeamTaskDependencyDB(
        id="dependency-a-b",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        source_task_id="task-source",
        target_task_id="task-target",
        source_team_id="runtime-team-a",
        target_team_id="runtime-team-b",
        owner_ref="slot-b",
        gate_ref="quality-gate@1",
        required_artifact_refs=["artifact-a"],
        status="blocked",
        blocking_reason="awaiting_review",
    )
    relation = OrganizationRelationDB(
        id="handoff-a-b",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        relation_key="handoff-a-b",
        kind="handoff",
        source_unit_id="team-a",
        target_unit_id="team-b",
        handoff_definition_key="delivery-handoff",
        handoff_definition_version=1,
    )
    artifact = ArtifactDB(
        id="artifact-a",
        latest_version_id="artifact-version-a",
        latest_sha256="a" * 64,
        latest_filename="review.json",
        artifact_metadata={"secret": "must-not-be-selected"},
    )
    artifact_version = ArtifactVersionDB(
        id="artifact-version-a",
        artifact_id="artifact-a",
        version_number=2,
        storage_path="must-not-be-selected",
        original_filename="review.json",
        media_type="application/json",
        sha256="a" * 64,
    )
    snapshot = OrganizationTopologySnapshotDB(
        id="snapshot-runtime",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        revision=2,
        definition_revision="e" * 64,
        snapshot_hash="s" * 64,
    )
    session = EntityRowsSession(
        {
            OrganizationInstanceDB: [_organization()],
            OrganizationUnitDB: units,
            OrganizationTeamLinkDB: links,
            OrganizationRoleSlotDB: [slot],
            OrganizationRoleAssignmentDB: [assignment],
            OrganizationRelationDB: [relation],
            OrganizationTopologySnapshotDB: [snapshot],
            TaskDB: tasks,
            CrossTeamTaskDependencyDB: [dependency],
            ArtifactDB: [artifact],
            ArtifactVersionDB: [artifact_version],
        }
    )

    result = SqlOrganizationTopologyReadRepository(session).load_topology_snapshot(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        include_runtime_overlay=True,
        cursor=None,
        limit=10,
        max_depth=4,
        filters={},
    )

    runtime_by_node = {row["node_id"]: row for row in result["runtime_nodes"]}
    assert result["runtime_definition_revision"] == "e" * 64
    assert result["runtime_snapshot_hash"] == "s" * 64
    assert runtime_by_node["team-a"]["status"]["state"] == "active"
    target_status = runtime_by_node["team-b"]["status"]
    assert target_status == {
        "state": "blocked",
        "label": "Blocked",
        "reason_code": "WAITING_FOR_REVIEW",
        "capacity_used": 1,
        "capacity_limit": 4,
        "blocker_count": 2,
        "gate_count": 1,
        "handoff_count": 1,
        "drift": True,
    }
    assert runtime_by_node["slot-b"]["status"]["capacity_limit"] == 4
    assert runtime_by_node["assignment-b"]["status"]["state"] == "blocked"
    assert runtime_by_node["team-b"]["latest_artifacts"] == [
        {
            "artifact_id": "artifact-a",
            "version": "2",
            "digest": "a" * 64,
            "label": "review.json",
        }
    ]

    statements_by_entity = {}
    for statement in session.statements:
        entity = statement.column_descriptions[0]["entity"]
        statements_by_entity.setdefault(entity, []).append(str(statement))
    for entity in (TaskDB, CrossTeamTaskDependencyDB):
        runtime_sql = "\n".join(statements_by_entity[entity])
        assert f"{entity.__tablename__}.tenant_id" in runtime_sql
        assert f"{entity.__tablename__}.project_id" in runtime_sql
        assert f"{entity.__tablename__}.organization_id" in runtime_sql
    task_sql = "\n".join(statements_by_entity[TaskDB])
    assert "tasks.description" not in task_sql
    assert "tasks.last_output" not in task_sql
    assert "tasks.worker_execution_context" not in task_sql
    assert "tasks.verification_status" not in task_sql
    artifact_sql = "\n".join(statements_by_entity[ArtifactDB])
    version_sql = "\n".join(statements_by_entity[ArtifactVersionDB])
    assert "artifacts.artifact_metadata" not in artifact_sql
    assert "artifact_versions.storage_path" not in version_sql
