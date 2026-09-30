"""Organization operation ledger, audit outbox and cross-team task dependencies."""

from __future__ import annotations

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .organization_columns import json_column


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
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
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
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
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
        sa_column=json_column(list),
    )
    due_at: float | None = None
    status: str = Field(default="pending", index=True, max_length=16)
    blocking_reason: str | None = Field(default=None, max_length=512)
    escalation_policy: str | None = Field(default=None, max_length=191)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


__all__ = [
    "OrganizationOperationDB",
    "OrganizationAuditOutboxDB",
    "CrossTeamTaskDependencyDB",
]
