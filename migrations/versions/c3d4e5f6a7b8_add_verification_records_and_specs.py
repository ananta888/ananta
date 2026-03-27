"""add verification records and specs

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("verification_spec", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("verification_status", sa.JSON(), nullable=True))

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("verification_spec", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("verification_status", sa.JSON(), nullable=True))

    with op.batch_alter_table("plan_nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("verification_spec", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("verification_status", sa.JSON(), nullable=True))

    op.create_table(
        "verification_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("verification_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repair_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalation_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("verification_records", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_verification_records_task_id"), ["task_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_verification_records_goal_id"), ["goal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_verification_records_trace_id"), ["trace_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_verification_records_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("verification_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_verification_records_created_at"))
        batch_op.drop_index(batch_op.f("ix_verification_records_trace_id"))
        batch_op.drop_index(batch_op.f("ix_verification_records_goal_id"))
        batch_op.drop_index(batch_op.f("ix_verification_records_task_id"))
    op.drop_table("verification_records")

    with op.batch_alter_table("plan_nodes", schema=None) as batch_op:
        batch_op.drop_column("verification_status")
        batch_op.drop_column("verification_spec")

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.drop_column("verification_status")
        batch_op.drop_column("verification_spec")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("verification_status")
        batch_op.drop_column("verification_spec")
