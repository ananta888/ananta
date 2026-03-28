"""add task execution context columns

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name in _existing_columns(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.add_column(column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name in existing_indexes:
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(index_name, columns, unique=False)


def upgrade() -> None:
    _add_column_if_missing("tasks", sa.Column("context_bundle_id", sa.String(), nullable=True))
    _add_column_if_missing("tasks", sa.Column("worker_execution_context", sa.JSON(), nullable=True))
    _add_column_if_missing("tasks", sa.Column("current_worker_job_id", sa.String(), nullable=True))
    _create_index_if_missing("tasks", "ix_tasks_context_bundle_id", ["context_bundle_id"])
    _create_index_if_missing("tasks", "ix_tasks_current_worker_job_id", ["current_worker_job_id"])

    _add_column_if_missing("archived_tasks", sa.Column("context_bundle_id", sa.String(), nullable=True))
    _add_column_if_missing("archived_tasks", sa.Column("worker_execution_context", sa.JSON(), nullable=True))
    _add_column_if_missing("archived_tasks", sa.Column("current_worker_job_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_current_worker_job_id")
        batch_op.drop_index("ix_tasks_context_bundle_id")
        batch_op.drop_column("current_worker_job_id")
        batch_op.drop_column("worker_execution_context")
        batch_op.drop_column("context_bundle_id")

    with op.batch_alter_table("archived_tasks", schema=None) as batch_op:
        batch_op.drop_column("current_worker_job_id")
        batch_op.drop_column("worker_execution_context")
        batch_op.drop_column("context_bundle_id")
