"""Add restart-safe Hub workflow-control bindings.

Revision ID: q1r2s3t4u5v6
Revises: p1q2r3s4t5u6
Create Date: 2026-07-13 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "q1r2s3t4u5v6"
down_revision: str | Sequence[str] | None = "p1q2r3s4t5u6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workflow_control_bindings" not in tables:
        _create_control_bindings()
    if "workflow_command_nonces" not in tables:
        _create_command_nonces()


def _create_control_bindings() -> None:
    op.create_table(
        "workflow_control_bindings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("runtime_id", sa.String(), nullable=False),
        sa.Column("plan_hash", sa.String(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("checkpoint_id", sa.String(), nullable=False),
        sa.Column("workflow_request", sa.JSON(), nullable=False),
        sa.Column("last_status", sa.JSON(), nullable=False),
        sa.Column("runtime_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runtime_checkpoint_ref", sa.String(), nullable=False),
        sa.Column("command_claim", sa.String(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id",
            name="uq_workflow_control_binding_workflow",
        ),
        sa.UniqueConstraint("run_id", name="uq_workflow_control_binding_run"),
    )
    for name, columns in {
        "ix_workflow_control_bindings_tenant_id": ("tenant_id",),
        "ix_workflow_control_bindings_subject_id": ("subject_id",),
        "ix_workflow_control_bindings_workflow_id": ("workflow_id",),
        "ix_workflow_control_bindings_run_id": ("run_id",),
        "ix_workflow_control_bindings_runtime_id": ("runtime_id",),
        "ix_workflow_control_bindings_created_at": ("created_at",),
        "ix_workflow_control_bindings_updated_at": ("updated_at",),
        "ix_workflow_control_bindings_command_claim": ("command_claim",),
        "ix_workflow_control_bindings_owner": (
            "tenant_id",
            "subject_id",
            "workflow_id",
        ),
    }.items():
        op.create_index(name, "workflow_control_bindings", list(columns), unique=False)


def _create_command_nonces() -> None:
    op.create_table(
        "workflow_command_nonces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("nonce_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_command_nonces_tenant_id",
        "workflow_command_nonces",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_command_nonces_expires_at",
        "workflow_command_nonces",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_command_nonces_consumed_at",
        "workflow_command_nonces",
        ["consumed_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workflow_command_nonces" in tables:
        op.drop_table("workflow_command_nonces")
    if "workflow_control_bindings" in tables:
        op.drop_table("workflow_control_bindings")
