"""backfill missing audit_logs traceability columns

Revision ID: b1c2d3e4f5a6
Revises: a4c5e6f7a8b9
Create Date: 2026-05-10 17:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a4c5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _existing_columns(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {item["name"] for item in inspect(op.get_bind()).get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name in _existing_columns(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.add_column(column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], *, unique: bool = False) -> None:
    if index_name in _index_names(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(index_name, columns, unique=unique)


def upgrade() -> None:
    table_name = "audit_logs"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(table_name, sa.Column("trace_id", sa.String(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("goal_id", sa.String(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("task_id", sa.String(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("plan_id", sa.String(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("verification_record_id", sa.String(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("prev_hash", sa.String(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("record_hash", sa.String(), nullable=True))

    _create_index_if_missing(table_name, "ix_audit_logs_trace_id", ["trace_id"])
    _create_index_if_missing(table_name, "ix_audit_logs_goal_id", ["goal_id"])
    _create_index_if_missing(table_name, "ix_audit_logs_task_id", ["task_id"])
    _create_index_if_missing(table_name, "ix_audit_logs_plan_id", ["plan_id"])
    _create_index_if_missing(table_name, "ix_audit_logs_verification_record_id", ["verification_record_id"])
    _create_index_if_missing(table_name, "ix_audit_logs_record_hash", ["record_hash"])


def downgrade() -> None:
    # compatibility backfill; intentionally non-destructive
    pass
