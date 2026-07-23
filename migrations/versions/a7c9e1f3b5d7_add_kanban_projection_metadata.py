"""add Kanban projection metadata to existing tasks

Revision ID: a7c9e1f3b5d7
Revises: e68df4c5b7a9
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a7c9e1f3b5d7"
down_revision: str | None = "e68df4c5b7a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("kanban_position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tasks",
        sa.Column("kanban_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_tasks_kanban_position", "tasks", ["kanban_position"])
    op.create_index("ix_tasks_kanban_revision", "tasks", ["kanban_revision"])
    op.add_column(
        "archived_tasks",
        sa.Column("kanban_position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "archived_tasks",
        sa.Column("kanban_revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("archived_tasks", "kanban_revision")
    op.drop_column("archived_tasks", "kanban_position")
    op.drop_index("ix_tasks_kanban_revision", table_name="tasks")
    op.drop_index("ix_tasks_kanban_position", table_name="tasks")
    op.drop_column("tasks", "kanban_revision")
    op.drop_column("tasks", "kanban_position")

