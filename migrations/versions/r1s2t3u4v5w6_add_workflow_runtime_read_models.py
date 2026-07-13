"""Add durable operations-UI workflow runtime projections.

Revision ID: r1s2t3u4v5w6
Revises: q1r2s3t4u5v6
Create Date: 2026-07-13 16:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "r1s2t3u4v5w6"
down_revision: str | Sequence[str] | None = "q1r2s3t4u5v6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "workflow_runtime_read_models" in set(inspect(bind).get_table_names()):
        return
    op.create_table(
        "workflow_runtime_read_models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("runtime", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            name="uq_workflow_runtime_read_model_run",
        ),
    )
    for name, columns in {
        "ix_workflow_runtime_read_models_tenant_id": ("tenant_id",),
        "ix_workflow_runtime_read_models_run_id": ("run_id",),
        "ix_workflow_runtime_read_models_workflow_id": ("workflow_id",),
        "ix_workflow_runtime_read_models_runtime": ("runtime",),
        "ix_workflow_runtime_read_models_mode": ("mode",),
        "ix_workflow_runtime_read_models_status": ("status",),
        "ix_workflow_runtime_read_models_updated_at": ("updated_at",),
        "ix_workflow_runtime_read_models_tenant_updated": (
            "tenant_id",
            "updated_at",
        ),
    }.items():
        op.create_index(name, "workflow_runtime_read_models", list(columns), unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "workflow_runtime_read_models" in set(inspect(bind).get_table_names()):
        op.drop_table("workflow_runtime_read_models")
