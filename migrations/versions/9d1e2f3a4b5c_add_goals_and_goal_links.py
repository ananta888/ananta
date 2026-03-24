"""add goals and goal task links

Revision ID: 9d1e2f3a4b5c
Revises: 2a4b6c8d0e1f
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d1e2f3a4b5c"
down_revision: Union[str, Sequence[str], None] = "2a4b6c8d0e1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("goal", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="received"),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="ui"),
        sa.Column("requested_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("team_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("context", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("constraints", sa.JSON(), nullable=True),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=True),
        sa.Column("execution_preferences", sa.JSON(), nullable=True),
        sa.Column("visibility", sa.JSON(), nullable=True),
        sa.Column("workflow_defaults", sa.JSON(), nullable=True),
        sa.Column("workflow_overrides", sa.JSON(), nullable=True),
        sa.Column("workflow_effective", sa.JSON(), nullable=True),
        sa.Column("workflow_provenance", sa.JSON(), nullable=True),
        sa.Column("readiness", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("goals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_goals_trace_id"), ["trace_id"], unique=False)

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("goal_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("goal_trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index(batch_op.f("ix_tasks_goal_id"), ["goal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_goal_trace_id"), ["goal_trace_id"], unique=False)

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("goal_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("goal_trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.drop_column("goal_trace_id")
        batch_op.drop_column("goal_id")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tasks_goal_trace_id"))
        batch_op.drop_index(batch_op.f("ix_tasks_goal_id"))
        batch_op.drop_column("goal_trace_id")
        batch_op.drop_column("goal_id")

    with op.batch_alter_table("goals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_goals_trace_id"))
    op.drop_table("goals")
