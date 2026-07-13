"""Add workflow rollout policy, audit and authorization grants.

Revision ID: t1u2v3w4x5y6
Revises: s1t2u3v4w5x6
Create Date: 2026-07-13 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "t1u2v3w4x5y6"
down_revision: str | Sequence[str] | None = "s1t2u3v4w5x6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workflow_runtime_rollout_policies" not in tables:
        op.create_table(
            "workflow_runtime_rollout_policies",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scope_type", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False, server_default=""),
            sa.Column("profile_id", sa.String(), nullable=False, server_default=""),
            sa.Column("workflow_id", sa.String(), nullable=False, server_default=""),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("policy", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        _indexes(
            "workflow_runtime_rollout_policies",
            {
                "ix_workflow_runtime_rollout_policies_scope_type": ("scope_type",),
                "ix_workflow_runtime_rollout_policies_project_id": ("project_id",),
                "ix_workflow_runtime_rollout_policies_tenant_id": ("tenant_id",),
                "ix_workflow_runtime_rollout_policies_profile_id": ("profile_id",),
                "ix_workflow_runtime_rollout_policies_workflow_id": ("workflow_id",),
                "ix_workflow_runtime_rollout_policies_policy_version": (
                    "policy_version",
                ),
                "ix_workflow_runtime_rollout_policies_mode": ("mode",),
                "ix_workflow_runtime_rollout_policies_created_at": ("created_at",),
                "ix_workflow_runtime_rollout_policies_updated_at": ("updated_at",),
                "ix_workflow_runtime_rollout_scope": (
                    "project_id",
                    "tenant_id",
                    "profile_id",
                    "workflow_id",
                ),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_runtime_rollout_audit" not in tables:
        op.create_table(
            "workflow_runtime_rollout_audit",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("scope_type", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False, server_default=""),
            sa.Column("profile_id", sa.String(), nullable=False, server_default=""),
            sa.Column("workflow_id", sa.String(), nullable=False, server_default=""),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("actor_id", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("occurred_at", sa.Float(), nullable=False),
            sa.Column("event", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        _indexes(
            "workflow_runtime_rollout_audit",
            {
                "ix_workflow_runtime_rollout_audit_scope_key": ("scope_key",),
                "ix_workflow_runtime_rollout_audit_scope_type": ("scope_type",),
                "ix_workflow_runtime_rollout_audit_project_id": ("project_id",),
                "ix_workflow_runtime_rollout_audit_tenant_id": ("tenant_id",),
                "ix_workflow_runtime_rollout_audit_profile_id": ("profile_id",),
                "ix_workflow_runtime_rollout_audit_workflow_id": ("workflow_id",),
                "ix_workflow_runtime_rollout_audit_action": ("action",),
                "ix_workflow_runtime_rollout_audit_actor_id": ("actor_id",),
                "ix_workflow_runtime_rollout_audit_reason_code": ("reason_code",),
                "ix_workflow_runtime_rollout_audit_occurred_at": ("occurred_at",),
                "ix_workflow_runtime_rollout_audit_scope_time": (
                    "scope_key",
                    "occurred_at",
                ),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_authorization_grants" not in tables:
        op.create_table(
            "workflow_authorization_grants",
            sa.Column("envelope_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=False),
            sa.Column("plan_hash", sa.String(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("grant_digest", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("issued_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("revocation_reason", sa.String(), nullable=False, server_default=""),
            sa.PrimaryKeyConstraint("envelope_id"),
        )
        _indexes(
            "workflow_authorization_grants",
            {
                "ix_workflow_authorization_grants_tenant_id": ("tenant_id",),
                "ix_workflow_authorization_grants_workflow_id": ("workflow_id",),
                "ix_workflow_authorization_grants_run_id": ("run_id",),
                "ix_workflow_authorization_grants_step_id": ("step_id",),
                "ix_workflow_authorization_grants_plan_hash": ("plan_hash",),
                "ix_workflow_authorization_grants_policy_version": (
                    "policy_version",
                ),
                "ix_workflow_authorization_grants_status": ("status",),
                "ix_workflow_authorization_grants_issued_at": ("issued_at",),
                "ix_workflow_authorization_grants_expires_at": ("expires_at",),
                "ix_workflow_authorization_grants_updated_at": ("updated_at",),
                "ix_workflow_authorization_grants_revoked_at": ("revoked_at",),
                "ix_workflow_authorization_grants_binding": (
                    "tenant_id",
                    "run_id",
                    "step_id",
                    "status",
                ),
            },
        )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "workflow_authorization_grants",
        "workflow_runtime_rollout_audit",
        "workflow_runtime_rollout_policies",
    ):
        if table in tables:
            op.drop_table(table)


def _indexes(table: str, values: dict[str, tuple[str, ...]]) -> None:
    for name, columns in values.items():
        op.create_index(name, table, list(columns), unique=False)
