"""Immutable, tenant/project-scoped organization definition revisions."""

from __future__ import annotations

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .organization_columns import json_column


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
    appendix_refs: list[str] = Field(default_factory=list, sa_column=json_column(list))
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
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
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
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
    steps_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=json_column(list))
    checks_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    required_capabilities: list[str] = Field(default_factory=list, sa_column=json_column(list))
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
    limits_json: dict[str, int] = Field(default_factory=dict, sa_column=json_column(dict))
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
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
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
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    referenced_definition_hashes: dict[str, str] = Field(default_factory=dict, sa_column=json_column(dict))
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
    required_artifact_kinds: list[str] = Field(default_factory=list, sa_column=json_column(list))
    acceptance_gate_ref: str = Field(max_length=255)
    definition_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    created_at: float = Field(default_factory=time.time)


__all__ = [
    "RoleTemplateRevisionDB",
    "TeamBlueprintRevisionDB",
    "WorkflowDefinitionRevisionDB",
    "OrganizationLimitProfileRevisionDB",
    "OrganizationPolicyRevisionDB",
    "OrganizationBlueprintRevisionDB",
    "OrganizationHandoffDefinitionRevisionDB",
]
