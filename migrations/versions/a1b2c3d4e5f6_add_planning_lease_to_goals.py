"""add planning_lease_expires_at to goals (PRI-004)

Revision ID: a1b2c3d4e5f6
Revises: d7e8f9a0b1c2
Create Date: 2026-05-22 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name: str) -> set[str]:
    return {col["name"] for col in inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {idx["name"] for idx in inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    if "planning_lease_expires_at" not in _existing_columns("goals"):
        with op.batch_alter_table("goals") as batch_op:
            batch_op.add_column(sa.Column("planning_lease_expires_at", sa.Float(), nullable=True))
    if "ix_goals_planning_lease_expires_at" not in _index_names("goals"):
        with op.batch_alter_table("goals") as batch_op:
            batch_op.create_index("ix_goals_planning_lease_expires_at", ["planning_lease_expires_at"])


def downgrade() -> None:
    with op.batch_alter_table("goals") as batch_op:
        batch_op.drop_index("ix_goals_planning_lease_expires_at")
        batch_op.drop_column("planning_lease_expires_at")
