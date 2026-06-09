"""add pattern_hints column to blueprint_workflow_steps (PAT-013)

Revision ID: g1h2i3j4k5l6
Revises: f7a8b9c0d1e2
Create Date: 2026-06-09 00:00:00.000000

PAT-013: Blueprint workflow steps may carry optional pattern_hints so the
planner (BlueprintPlanningAdapter) can forward validated pattern constraints
to subtasks and worker execution contexts without re-querying the catalog.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name: str) -> set[str]:
    return {col["name"] for col in inspect(op.get_bind()).get_columns(table_name)}


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "blueprint_workflow_steps" not in _existing_tables():
        return
    cols = _existing_columns("blueprint_workflow_steps")
    if "pattern_hints" not in cols:
        op.add_column(
            "blueprint_workflow_steps",
            sa.Column("pattern_hints", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if "blueprint_workflow_steps" not in _existing_tables():
        return
    cols = _existing_columns("blueprint_workflow_steps")
    if "pattern_hints" in cols:
        op.drop_column("blueprint_workflow_steps", "pattern_hints")
