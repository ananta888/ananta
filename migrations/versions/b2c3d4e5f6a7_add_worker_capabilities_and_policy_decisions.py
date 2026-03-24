"""add worker capabilities and policy decisions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("worker_roles", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("capabilities", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("execution_limits", sa.JSON(), nullable=True))

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("task_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("required_capabilities", sa.JSON(), nullable=True))

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("task_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("required_capabilities", sa.JSON(), nullable=True))

    op.create_table(
        "policy_decisions",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("goal_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("decision_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("worker_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("policy_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("policy_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("policy_decisions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_policy_decisions_task_id"), ["task_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_policy_decisions_goal_id"), ["goal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_policy_decisions_trace_id"), ["trace_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_policy_decisions_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("policy_decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_policy_decisions_created_at"))
        batch_op.drop_index(batch_op.f("ix_policy_decisions_trace_id"))
        batch_op.drop_index(batch_op.f("ix_policy_decisions_goal_id"))
        batch_op.drop_index(batch_op.f("ix_policy_decisions_task_id"))
    op.drop_table("policy_decisions")

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.drop_column("required_capabilities")
        batch_op.drop_column("task_kind")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("required_capabilities")
        batch_op.drop_column("task_kind")

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("execution_limits")
        batch_op.drop_column("capabilities")
        batch_op.drop_column("worker_roles")
