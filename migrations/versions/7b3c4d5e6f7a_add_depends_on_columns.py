"""add depends_on columns

Revision ID: 7b3c4d5e6f7a
Revises: 6f9a1b2c3d4e
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "7b3c4d5e6f7a"
down_revision: Union[str, Sequence[str], None] = "6f9a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_json_column_if_missing(table: str, column: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(table):
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column in existing:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.add_column(sa.Column(column, sa.JSON(), nullable=True))


def _backfill_json_array(table: str, column: str) -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(sa.text(f"UPDATE {table} SET {column} = '[]'::json WHERE {column} IS NULL"))
    else:
        op.execute(sa.text(f"UPDATE {table} SET {column} = '[]' WHERE {column} IS NULL"))


def upgrade() -> None:
    _add_json_column_if_missing("tasks", "depends_on")
    _add_json_column_if_missing("archived_tasks", "depends_on")
    _backfill_json_array("tasks", "depends_on")
    _backfill_json_array("archived_tasks", "depends_on")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("archived_tasks", "tasks"):
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if "depends_on" not in existing:
            continue
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("depends_on")
