"""add missing runtime_targets and selection columns

Revision ID: c6d7e8f9a0b1
Revises: b1c2d3e4f5a6
Create Date: 2026-05-13 19:55:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _existing_columns(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name in _existing_columns(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.add_column(column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name not in _existing_columns(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.drop_column(column_name)


def upgrade() -> None:
    _add_column_if_missing(
        "agents",
        sa.Column("runtime_targets", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )

    _add_column_if_missing(
        "worker_jobs",
        sa.Column("selected_worker_id", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_jobs",
        sa.Column("selected_worker_kind", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_jobs",
        sa.Column("selected_runtime_target_id", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_jobs",
        sa.Column("selected_runtime_kind", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_jobs",
        sa.Column("selection_mode", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_jobs",
        sa.Column("selection_decision_ref", sa.String(), nullable=True),
    )

    _add_column_if_missing(
        "worker_results",
        sa.Column("actual_worker_id", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_results",
        sa.Column("actual_worker_kind", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_results",
        sa.Column("actual_runtime_target_id", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_results",
        sa.Column("actual_runtime_kind", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        "worker_results",
        sa.Column("selection_reason", sa.String(), nullable=True),
    )


def downgrade() -> None:
    _drop_column_if_exists("worker_results", "selection_reason")
    _drop_column_if_exists("worker_results", "actual_runtime_kind")
    _drop_column_if_exists("worker_results", "actual_runtime_target_id")
    _drop_column_if_exists("worker_results", "actual_worker_kind")
    _drop_column_if_exists("worker_results", "actual_worker_id")

    _drop_column_if_exists("worker_jobs", "selection_decision_ref")
    _drop_column_if_exists("worker_jobs", "selection_mode")
    _drop_column_if_exists("worker_jobs", "selected_runtime_kind")
    _drop_column_if_exists("worker_jobs", "selected_runtime_target_id")
    _drop_column_if_exists("worker_jobs", "selected_worker_kind")
    _drop_column_if_exists("worker_jobs", "selected_worker_id")

    _drop_column_if_exists("agents", "runtime_targets")
