"""Add Hub-controlled workflow Worker assignment bindings.

Revision ID: w1x2y3z4a5b6
Revises: v1w2x3y4z5a6
Create Date: 2026-07-14 00:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w1x2y3z4a5b6"
down_revision: str | Sequence[str] | None = "v1w2x3y4z5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_worker_assignments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("attempt_id", sa.String(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("hub_task_id", sa.String(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("worker_url", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            name="uq_workflow_worker_assignment_step",
        ),
    )
    for column in (
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "attempt_id",
        "hub_task_id",
        "worker_id",
        "assigned_at",
    ):
        op.create_index(
            f"ix_workflow_worker_assignments_{column}",
            "workflow_worker_assignments",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_workflow_worker_assignment_worker",
        "workflow_worker_assignments",
        ["worker_id", "worker_url"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("workflow_worker_assignments")
