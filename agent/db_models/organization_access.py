"""Organization administration grants and admission exceptions."""

from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .organization_columns import json_column


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
        sa_column=json_column(dict),
    )
    capability_gaps: list[str] = Field(
        default_factory=list,
        sa_column=json_column(list),
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


__all__ = [
    "OrganizationAdminGrantDB",
    "OrganizationTopologyPatchGrantDB",
    "OrganizationAdmissionExceptionDB",
]
