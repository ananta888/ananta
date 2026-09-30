"""Organization instances and their unit, team, role and relation topology."""

from __future__ import annotations

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .organization_columns import json_column


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
    assignment_policy: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    separation_of_duties: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    overlays: list[str] = Field(default_factory=list, sa_column=json_column(list))
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
    assignment_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
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


__all__ = [
    "OrganizationInstanceDB",
    "OrganizationUnitDB",
    "OrganizationTeamLinkDB",
    "OrganizationRoleSlotDB",
    "OrganizationRoleAssignmentDB",
    "OrganizationRelationDB",
    "OrganizationMembershipDB",
]
