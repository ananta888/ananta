"""add plans and plan nodes

Revision ID: a1b2c3d4e5f6
Revises: 9d1e2f3a4b5c
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9d1e2f3a4b5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("goal_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="draft"),
        sa.Column("planning_mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="auto_planner"),
        sa.Column("rationale", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_plans_goal_id"), ["goal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_plans_trace_id"), ["trace_id"], unique=False)

    op.create_table(
        "plan_nodes",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plan_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("node_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("priority", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="Medium"),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="draft"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depends_on", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.JSON(), nullable=True),
        sa.Column("editable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("materialized_task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("plan_nodes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_plan_nodes_plan_id"), ["plan_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_plan_nodes_node_key"), ["node_key"], unique=False)

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("plan_node_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index(batch_op.f("ix_tasks_plan_id"), ["plan_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_plan_node_id"), ["plan_node_id"], unique=False)

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("plan_node_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.drop_column("plan_node_id")
        batch_op.drop_column("plan_id")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tasks_plan_node_id"))
        batch_op.drop_index(batch_op.f("ix_tasks_plan_id"))
        batch_op.drop_column("plan_node_id")
        batch_op.drop_column("plan_id")

    with op.batch_alter_table("plan_nodes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_plan_nodes_node_key"))
        batch_op.drop_index(batch_op.f("ix_plan_nodes_plan_id"))
    op.drop_table("plan_nodes")

    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_plans_trace_id"))
        batch_op.drop_index(batch_op.f("ix_plans_goal_id"))
    op.drop_table("plans")
