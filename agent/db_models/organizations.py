"""Normalized Hub-owned organization and definition persistence models."""

from __future__ import annotations

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _json_column(default: Any) -> sa.Column:
    return sa.Column(sa.JSON(), nullable=False, default=default)


class RoleTemplateRevisionDB(SQLModel, table=True):
    __tablename__ = "role_template_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_role_template_revisions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "definition_key",
            "version",
            name="uq_role_template_revision_scope_key_version",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_role_template_revision_lifecycle",
        ),
        sa.CheckConstraint("version >= 1", name="ck_role_template_revision_version"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    definition_key: str = Field(index=True, max_length=191)
    version: int = Field(ge=1)
    lifecycle: str = Field(default="draft", max_length=16)
    content_hash: str = Field(index=True, max_length=64)
    prompt_hash: str = Field(max_length=64)
    appendix_refs: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_by: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    activated_at: float | None = None


class TeamBlueprintRevisionDB(SQLModel, table=True):
    __tablename__ = "team_blueprint_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_team_blueprint_revisions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "definition_key",
            "version",
            name="uq_team_blueprint_revision_scope_key_version",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_team_blueprint_revision_lifecycle",
        ),
        sa.CheckConstraint(
            "version >= 1 AND (workflow_definition_version IS NULL OR workflow_definition_version >= 1)",
            name="ck_team_blueprint_revision_versions",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    definition_key: str = Field(index=True, max_length=191)
    version: int = Field(ge=1)
    lifecycle: str = Field(default="draft", max_length=16)
    content_hash: str = Field(index=True, max_length=64)
    workflow_definition_key: str | None = Field(default=None, max_length=191)
    workflow_definition_version: int | None = Field(default=None, ge=1)
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    legacy_blueprint_id: str | None = Field(default=None, foreign_key="team_blueprints.id", index=True)
    created_by: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    activated_at: float | None = None


class WorkflowDefinitionRevisionDB(SQLModel, table=True):
    __tablename__ = "workflow_definition_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_workflow_definition_revisions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "definition_key",
            "version",
            name="uq_workflow_definition_revision_scope_key_version",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_workflow_definition_revision_lifecycle",
        ),
        sa.CheckConstraint("version >= 1", name="ck_workflow_definition_revision_version"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    definition_key: str = Field(index=True, max_length=191)
    version: int = Field(ge=1)
    lifecycle: str = Field(default="draft", max_length=16)
    content_hash: str = Field(index=True, max_length=64)
    mode: str = Field(max_length=64)
    default_failure_policy: str = Field(max_length=64)
    steps_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=_json_column(list))
    checks_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    required_capabilities: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    created_by: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    activated_at: float | None = None


class OrganizationLimitProfileRevisionDB(SQLModel, table=True):
    __tablename__ = "organization_limit_profile_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_limit_profiles_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "policy_key",
            "revision",
            name="uq_organization_limit_profile_scope_key_revision",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_organization_limit_profile_revision"),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_organization_limit_profile_lifecycle",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    policy_key: str = Field(index=True, max_length=191)
    revision: int = Field(ge=1)
    profile_hash: str = Field(index=True, max_length=64)
    lifecycle: str = Field(default="active", max_length=16)
    limits_json: dict[str, int] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_at: float = Field(default_factory=time.time)


class OrganizationPolicyRevisionDB(SQLModel, table=True):
    __tablename__ = "organization_policy_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_policy_revisions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "policy_key",
            "revision",
            name="uq_organization_policy_revision_scope_key_revision",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_organization_policy_revision_lifecycle",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_organization_policy_revision_revision"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    policy_key: str = Field(index=True, max_length=191)
    revision: int = Field(ge=1)
    lifecycle: str = Field(default="draft", max_length=16)
    content_hash: str = Field(index=True, max_length=64)
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_at: float = Field(default_factory=time.time)


class OrganizationBlueprintRevisionDB(SQLModel, table=True):
    __tablename__ = "organization_blueprint_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_blueprint_revisions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "definition_key",
            "version",
            name="uq_organization_blueprint_revision_scope_key_version",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_organization_blueprint_revision_lifecycle",
        ),
        sa.CheckConstraint("version >= 1", name="ck_organization_blueprint_revision_version"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    definition_key: str = Field(index=True, max_length=191)
    version: int = Field(ge=1)
    lifecycle: str = Field(default="draft", max_length=16)
    content_hash: str = Field(index=True, max_length=64)
    limit_policy_ref: str = Field(max_length=255)
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    referenced_definition_hashes: dict[str, str] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_by: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    activated_at: float | None = None


class OrganizationHandoffDefinitionRevisionDB(SQLModel, table=True):
    __tablename__ = "organization_handoff_definition_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_handoff_definitions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "definition_key",
            "version",
            name="uq_organization_handoff_definition_scope_key_version",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'active', 'retired')",
            name="ck_organization_handoff_definition_lifecycle",
        ),
        sa.CheckConstraint("version >= 1", name="ck_organization_handoff_definition_version"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    definition_key: str = Field(index=True, max_length=191)
    version: int = Field(ge=1)
    lifecycle: str = Field(default="active", max_length=16)
    content_hash: str = Field(max_length=64)
    required_artifact_kinds: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    acceptance_gate_ref: str = Field(max_length=255)
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_at: float = Field(default_factory=time.time)


class OrganizationInstanceDB(SQLModel, table=True):
    __tablename__ = "organization_instances"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_instances_project",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            name="uq_organization_instance_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "idempotency_key",
            name="uq_organization_instance_scope_idempotency",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'validated', 'active', 'paused', 'completed', 'archived')",
            name="ck_organization_instance_lifecycle",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_organization_instance_lock_version"),
        sa.CheckConstraint(
            "definition_version >= 1 AND effective_limit_profile_revision >= 1",
            name="ck_organization_instance_definition_versions",
        ),
    )

    organization_id: str = Field(primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    name: str = Field(max_length=255)
    definition_key: str = Field(max_length=191)
    definition_version: int = Field(ge=1)
    definition_revision: str = Field(max_length=64)
    lifecycle: str = Field(default="draft", index=True, max_length=16)
    effective_limit_profile_ref: str = Field(max_length=255)
    effective_limit_profile_revision: int = Field(ge=1)
    effective_limit_profile_hash: str = Field(max_length=64)
    composition_mode: str = Field(max_length=16)
    plan_digest: str = Field(max_length=64)
    idempotency_key: str = Field(max_length=191)
    lock_version: int = Field(default=1, ge=1)
    created_by: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    archived_at: float | None = None


class OrganizationUnitDB(SQLModel, table=True):
    __tablename__ = "organization_units"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_units_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "parent_unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_organization_units_parent",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "project_id", "organization_id", "id", name="uq_organization_unit_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "unit_key", name="uq_organization_unit_scope_key"
        ),
        sa.CheckConstraint(
            "parent_unit_id IS NULL OR parent_unit_id <> id", name="ck_organization_unit_not_self_parent"
        ),
        sa.CheckConstraint(
            "unit_kind IN ('coordination_unit', 'value_stream', 'team')",
            name="ck_organization_unit_kind",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('planned', 'active', 'draining', 'archived')",
            name="ck_organization_unit_lifecycle",
        ),
        sa.CheckConstraint(
            "team_blueprint_version IS NULL OR team_blueprint_version >= 1",
            name="ck_organization_unit_team_blueprint_version",
        ),
        sa.CheckConstraint(
            "group_ordinal IS NULL OR group_ordinal >= 1",
            name="ck_organization_unit_group_ordinal",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    unit_key: str = Field(max_length=191)
    name: str = Field(max_length=255)
    unit_kind: str = Field(max_length=32)
    parent_unit_id: str | None = Field(default=None, index=True, max_length=191)
    team_blueprint_key: str | None = Field(default=None, max_length=191)
    team_blueprint_version: int | None = Field(default=None, ge=1)
    group_key: str | None = Field(default=None, max_length=191)
    group_ordinal: int | None = Field(default=None, ge=1)
    lifecycle: str = Field(default="planned", index=True, max_length=16)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class OrganizationTeamLinkDB(SQLModel, table=True):
    __tablename__ = "organization_team_links"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_organization_team_links_unit",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_organization_team_links_team", ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "unit_id", name="uq_organization_team_link_unit"
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "team_id", name="uq_organization_team_link_team"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('planned', 'active', 'draining', 'archived')",
            name="ck_organization_team_link_lifecycle",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    unit_id: str = Field(index=True, max_length=191)
    team_id: str = Field(index=True, max_length=191)
    lifecycle: str = Field(default="planned", index=True, max_length=16)
    created_at: float = Field(default_factory=time.time)
    activated_at: float | None = None
    archived_at: float | None = None


class OrganizationRoleSlotDB(SQLModel, table=True):
    __tablename__ = "organization_role_slots"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_organization_role_slots_unit",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "id", name="uq_organization_role_slot_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "unit_id",
            "slot_key",
            name="uq_organization_role_slot_unit_key",
        ),
        sa.CheckConstraint("min_count >= 0", name="ck_organization_role_slot_min_count"),
        sa.CheckConstraint("default_count >= min_count", name="ck_organization_role_slot_default_count"),
        sa.CheckConstraint(
            "max_count IS NULL OR max_count >= default_count", name="ck_organization_role_slot_max_count"
        ),
        sa.CheckConstraint("required = false OR min_count >= 1", name="ck_organization_role_slot_required_minimum"),
        sa.CheckConstraint("role_template_version >= 1", name="ck_organization_role_slot_template_version"),
        sa.CheckConstraint(
            "lifecycle IN ('planned', 'active', 'draining', 'archived')",
            name="ck_organization_role_slot_lifecycle",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    unit_id: str = Field(index=True, max_length=191)
    slot_key: str = Field(max_length=191)
    role_template_key: str = Field(max_length=191)
    role_template_version: int = Field(ge=1)
    required: bool = True
    min_count: int = Field(default=1, ge=0)
    default_count: int = Field(default=1, ge=0)
    max_count: int | None = Field(default=None, ge=0)
    assignment_policy: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    separation_of_duties: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    overlays: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    lifecycle: str = Field(default="active", max_length=16)
    created_at: float = Field(default_factory=time.time)


class OrganizationRoleAssignmentDB(SQLModel, table=True):
    __tablename__ = "organization_role_assignments"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "role_slot_id"],
            [
                "organization_role_slots.tenant_id",
                "organization_role_slots.project_id",
                "organization_role_slots.organization_id",
                "organization_role_slots.id",
            ],
            name="fk_organization_role_assignments_slot",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_url"], ["agents.url"], name="fk_organization_role_assignments_agent", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "id",
            name="uq_organization_role_assignment_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "role_slot_id",
            "agent_url",
            name="uq_organization_role_assignment_slot_agent",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('proposed', 'active', 'suspended', 'ended')",
            name="ck_organization_role_assignment_lifecycle",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    role_slot_id: str = Field(index=True, max_length=191)
    agent_url: str = Field(index=True, max_length=512)
    lifecycle: str = Field(default="proposed", index=True, max_length=16)
    assignment_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    assigned_at: float = Field(default_factory=time.time)
    ended_at: float | None = None


class OrganizationRelationDB(SQLModel, table=True):
    __tablename__ = "organization_relations"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "source_unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_organization_relations_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "target_unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_organization_relations_target",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "relation_key", name="uq_organization_relation_scope_key"
        ),
        sa.CheckConstraint("namespace = 'organization'", name="ck_organization_relation_namespace"),
        sa.CheckConstraint("source_unit_id <> target_unit_id", name="ck_organization_relation_distinct_endpoints"),
        sa.CheckConstraint(
            "handoff_definition_version IS NULL OR handoff_definition_version >= 1",
            name="ck_organization_relation_handoff_version",
        ),
        sa.CheckConstraint(
            "dependency_policy IN ('advisory', 'declared', 'gate')",
            name="ck_organization_relation_dependency_policy",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('planned', 'active', 'draining', 'archived')",
            name="ck_organization_relation_lifecycle",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    relation_key: str = Field(max_length=191)
    namespace: str = Field(default="organization", max_length=32)
    kind: str = Field(index=True, max_length=64)
    source_unit_id: str = Field(index=True, max_length=191)
    target_unit_id: str = Field(index=True, max_length=191)
    handoff_definition_key: str | None = Field(default=None, max_length=191)
    handoff_definition_version: int | None = Field(default=None, ge=1)
    dependency_policy: str = Field(default="advisory", max_length=32)
    escalation_policy: str | None = Field(default=None, max_length=191)
    lifecycle: str = Field(default="active", max_length=16)
    created_at: float = Field(default_factory=time.time)


class OrganizationMembershipDB(SQLModel, table=True):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_memberships_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "principal_id", name="uq_organization_membership_principal"
        ),
        sa.CheckConstraint(
            "membership_kind IN ('viewer', 'operator', 'organization_admin')",
            name="ck_organization_membership_kind",
        ),
    )

    membership_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    principal_id: str = Field(index=True, max_length=191)
    membership_kind: str = Field(max_length=32)
    expires_at: float | None = None
    created_at: float = Field(default_factory=time.time)


class OrganizationAdminGrantDB(SQLModel, table=True):
    __tablename__ = "organization_admin_grants"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_admin_grants_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_admin_grants_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "organization_id IS NOT NULL OR plan_digest IS NOT NULL",
            name="ck_organization_admin_grant_scope",
        ),
        sa.Index(
            "uq_organization_admin_grant_organization_scope",
            "tenant_id",
            "project_id",
            "organization_id",
            "principal_id",
            "grant_kind",
            unique=True,
            sqlite_where=sa.text("organization_id IS NOT NULL"),
            postgresql_where=sa.text("organization_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_organization_admin_grant_plan_scope",
            "tenant_id",
            "project_id",
            "plan_digest",
            "principal_id",
            "grant_kind",
            "idempotency_key",
            unique=True,
            sqlite_where=sa.text("organization_id IS NULL"),
            postgresql_where=sa.text("organization_id IS NULL"),
        ),
        sa.CheckConstraint(
            "organization_id IS NOT NULL OR idempotency_key IS NOT NULL",
            name="ck_organization_admin_grant_plan_idempotency",
        ),
    )

    grant_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    # A plan-bound pre-creation grant deliberately has no organization_id yet.
    # The nullable composite FK still enforces scope once an organization is bound.
    organization_id: str | None = Field(default=None, index=True, max_length=191)
    plan_digest: str | None = Field(default=None, index=True, max_length=64)
    principal_id: str = Field(index=True, max_length=191)
    grant_kind: str = Field(max_length=64)
    # Plan-bound one-shot grants may be reissued for the same unchanged plan
    # only under a fresh key. Instance-bound long-lived grants keep this null.
    idempotency_key: str | None = Field(default=None, index=True, max_length=191)
    policy_hash: str = Field(max_length=64)
    granted_by: str = Field(max_length=191)
    expires_at: float | None = None
    revoked_at: float | None = None
    created_at: float = Field(default_factory=time.time)


class OrganizationTopologyPatchGrantDB(SQLModel, table=True):
    """Short-lived one-shot authority for exactly one topology preview."""

    __tablename__ = "organization_topology_patch_grants"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_topology_patch_grants_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_topology_patch_grants_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_admin_grant_id"],
            ["organization_admin_grants.grant_id"],
            name="fk_topology_patch_grant_parent_admin_grant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "principal_id",
            "issue_idempotency_key",
            name="uq_topology_patch_grant_issue",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR revoked_at IS NOT NULL",
            name="ck_topology_patch_grant_consumed_revoked",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR (consumed_idempotency_key IS NOT NULL AND consumed_request_digest IS NOT NULL)",
            name="ck_topology_patch_grant_consumption_binding",
        ),
    )

    grant_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    principal_id: str = Field(index=True, max_length=191)
    parent_admin_grant_id: str = Field(index=True, max_length=191)
    patch_digest: str = Field(index=True, max_length=64)
    policy_hash: str = Field(max_length=64)
    limit_hash: str = Field(max_length=64)
    expected_revision: str = Field(max_length=191)
    issue_idempotency_key: str = Field(index=True, max_length=191)
    granted_by: str = Field(max_length=191)
    expires_at: float = Field(index=True)
    consumed_at: float | None = None
    consumed_idempotency_key: str | None = Field(default=None, max_length=191)
    consumed_request_digest: str | None = Field(default=None, max_length=64)
    revoked_at: float | None = None
    created_at: float = Field(default_factory=time.time)


class OrganizationAdmissionExceptionDB(SQLModel, table=True):
    """One-shot, principal-bound authorization for a custom composition."""

    __tablename__ = "organization_admission_exceptions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_admission_exceptions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "principal_id",
            "idempotency_key",
            name="uq_organization_admission_exception_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('issued', 'consumed', 'revoked')",
            name="ck_organization_admission_exception_status",
        ),
        sa.CheckConstraint(
            "definition_version >= 1 AND team_count >= 2",
            name="ck_organization_admission_exception_values",
        ),
    )

    exception_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=191,
    )
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    principal_id: str = Field(index=True, max_length=191)
    definition_key: str = Field(index=True, max_length=191)
    definition_version: int = Field(ge=1)
    definition_revision: str = Field(max_length=64)
    composition_digest: str = Field(index=True, max_length=64)
    policy_hash: str = Field(max_length=64)
    team_count: int = Field(ge=2)
    composition_json: dict[str, int] = Field(
        default_factory=dict,
        sa_column=_json_column(dict),
    )
    capability_gaps: list[str] = Field(
        default_factory=list,
        sa_column=_json_column(list),
    )
    reason: str = Field(max_length=512)
    idempotency_key: str = Field(max_length=191)
    request_digest: str = Field(max_length=64)
    status: str = Field(default="issued", index=True, max_length=16)
    issued_by: str = Field(max_length=191)
    created_at: float = Field(default_factory=time.time)
    expires_at: float = Field(index=True)
    consumed_at: float | None = None
    consumed_organization_id: str | None = Field(default=None, index=True, max_length=191)
    revoked_at: float | None = None


class OrganizationLayoutPreferenceDB(SQLModel, table=True):
    __tablename__ = "organization_layout_preferences"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_layout_preferences_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "principal_id",
            "projection_mode",
            name="uq_organization_layout_preference",
        ),
        sa.CheckConstraint("projection_mode IN ('hierarchy', 'graph')", name="ck_organization_layout_projection_mode"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    principal_id: str = Field(index=True, max_length=191)
    projection_mode: str = Field(max_length=16)
    definition_revision: str = Field(max_length=64)
    layout_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    updated_at: float = Field(default_factory=time.time)


class OrganizationTopologySnapshotDB(SQLModel, table=True):
    __tablename__ = "organization_topology_snapshots"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_topology_snapshots_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "revision", name="uq_organization_topology_snapshot_revision"
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "snapshot_hash", name="uq_organization_topology_snapshot_hash"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_organization_topology_snapshot_revision"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    revision: int = Field(ge=1)
    definition_revision: str = Field(max_length=64)
    snapshot_hash: str = Field(index=True, max_length=64)
    snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_at: float = Field(default_factory=time.time)


class OrganizationOperationDB(SQLModel, table=True):
    __tablename__ = "organization_operations"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_operations_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_operations_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "operation_kind", "idempotency_key", name="uq_organization_operation_idempotency"
        ),
        sa.CheckConstraint("status IN ('pending', 'applied', 'failed')", name="ck_organization_operation_status"),
    )

    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str | None = Field(default=None, index=True, max_length=191)
    operation_kind: str = Field(index=True, max_length=64)
    idempotency_key: str = Field(max_length=191)
    request_digest: str = Field(max_length=64)
    plan_digest: str = Field(max_length=64)
    expected_revision: str | None = Field(default=None, max_length=64)
    status: str = Field(default="pending", index=True, max_length=16)
    result_ref: str | None = Field(default=None, max_length=191)
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    created_at: float = Field(default_factory=time.time)
    applied_at: float | None = None


class OrganizationAuditOutboxDB(SQLModel, table=True):
    __tablename__ = "organization_audit_outbox"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_organization_audit_outbox_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_audit_outbox_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "project_id", "event_key", name="uq_organization_audit_outbox_event_key"),
        sa.CheckConstraint(
            "delivery_status IN ('pending', 'claimed', 'delivered', 'failed')",
            name="ck_organization_audit_outbox_status",
        ),
    )

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str | None = Field(default=None, index=True, max_length=191)
    event_key: str = Field(max_length=191)
    event_kind: str = Field(index=True, max_length=64)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    delivery_status: str = Field(default="pending", index=True, max_length=16)
    created_at: float = Field(default_factory=time.time)
    delivered_at: float | None = None


class CrossTeamTaskDependencyDB(SQLModel, table=True):
    __tablename__ = "cross_team_task_dependencies"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_cross_team_task_dependencies_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "source_task_id"],
            ["tasks.tenant_id", "tasks.project_id", "tasks.organization_id", "tasks.id"],
            name="fk_cross_team_dependency_source_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "target_task_id"],
            ["tasks.tenant_id", "tasks.project_id", "tasks.organization_id", "tasks.id"],
            name="fk_cross_team_dependency_target_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "source_team_id"],
            [
                "organization_team_links.tenant_id",
                "organization_team_links.project_id",
                "organization_team_links.organization_id",
                "organization_team_links.team_id",
            ],
            name="fk_cross_team_dependency_source_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "target_team_id"],
            [
                "organization_team_links.tenant_id",
                "organization_team_links.project_id",
                "organization_team_links.organization_id",
                "organization_team_links.team_id",
            ],
            name="fk_cross_team_dependency_target_team",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "source_task_id",
            "target_task_id",
            name="uq_cross_team_task_dependency",
        ),
        sa.CheckConstraint("source_task_id <> target_task_id", name="ck_cross_team_task_dependency_distinct_tasks"),
        sa.CheckConstraint(
            "status IN ('pending', 'blocked', 'ready', 'satisfied', 'cancelled')",
            name="ck_cross_team_task_dependency_status",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    source_task_id: str = Field(index=True, max_length=191)
    target_task_id: str = Field(index=True, max_length=191)
    source_team_id: str = Field(index=True, max_length=191)
    target_team_id: str = Field(index=True, max_length=191)
    owner_ref: str | None = Field(default=None, max_length=191)
    gate_ref: str | None = Field(default=None, max_length=191)
    required_artifact_refs: list[str] = Field(
        default_factory=list,
        sa_column=_json_column(list),
    )
    due_at: float | None = None
    status: str = Field(default="pending", index=True, max_length=16)
    blocking_reason: str | None = Field(default=None, max_length=512)
    escalation_policy: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


__all__ = [name for name in globals() if name.endswith("DB")]
