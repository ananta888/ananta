"""add runtime schema compatibility columns and tables

Revision ID: e0f1a2b3c4d5
Revises: d1e2f3a4b5c6
Create Date: 2026-04-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
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
    for table_name in ("tasks", "archived_tasks"):
        _add_column_if_missing(table_name, sa.Column("retrieval_intent", sa.String(), nullable=True))
        _add_column_if_missing(table_name, sa.Column("required_context_scope", sa.String(), nullable=True))
        _add_column_if_missing(table_name, sa.Column("preferred_bundle_mode", sa.String(), nullable=True))

    if _table_exists("goals"):
        mode_missing = "mode" not in _existing_columns("goals")
        _add_column_if_missing("goals", sa.Column("mode", sa.String(), nullable=False, server_default="generic"))
        _add_column_if_missing("goals", sa.Column("mode_data", sa.JSON(), nullable=True))
        _create_index_if_missing("goals", "ix_goals_mode", ["mode"])
        if mode_missing:
            with op.batch_alter_table("goals", schema=None) as batch_op:
                batch_op.alter_column("mode", server_default=None)

    _add_column_if_missing("verification_records", sa.Column("escalation_code", sa.String(), nullable=True))
    _add_column_if_missing("verification_records", sa.Column("escalation_details", sa.JSON(), nullable=True))

    if not _table_exists("playbooks"):
        op.create_table(
            "playbooks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("tasks", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("playbooks", "ix_playbooks_name", ["name"], unique=True)

    if not _table_exists("action_packs"):
        op.create_table(
            "action_packs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("capabilities", sa.JSON(), nullable=True),
            sa.Column("policy_config", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("action_packs", "ix_action_packs_name", ["name"], unique=True)


def downgrade() -> None:
    # Runtime compatibility migrations are intentionally conservative.
    pass
