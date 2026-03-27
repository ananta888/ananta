"""add task derivation and override columns

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-27 00:00:01.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manual_override_until", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("source_task_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("derivation_reason", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("derivation_depth", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manual_override_until", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("source_task_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("derivation_reason", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("derivation_depth", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column("derivation_depth", server_default=None)

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.alter_column("derivation_depth", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.drop_column("derivation_depth")
        batch_op.drop_column("derivation_reason")
        batch_op.drop_column("source_task_id")
        batch_op.drop_column("manual_override_until")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("derivation_depth")
        batch_op.drop_column("derivation_reason")
        batch_op.drop_column("source_task_id")
        batch_op.drop_column("manual_override_until")
