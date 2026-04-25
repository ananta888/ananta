"""add instruction layer tables

Revision ID: a4c5e6f7a8b9
Revises: e0f1a2b3c4d5
Create Date: 2026-04-25 23:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "a4c5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _index_names(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {item["name"] for item in inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], *, unique: bool = False) -> None:
    if index_name in _index_names(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(index_name, columns, unique=unique)


def upgrade() -> None:
    if not _table_exists("user_instruction_profiles"):
        op.create_table(
            "user_instruction_profiles",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_username", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("prompt_content", sa.Text(), nullable=False),
            sa.Column("profile_metadata", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["owner_username"], ["users.username"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_username", "name", name="uq_user_instruction_profiles_owner_name"),
        )
    _create_index_if_missing(
        "user_instruction_profiles",
        "ix_user_instruction_profiles_owner_username",
        ["owner_username"],
    )

    if not _table_exists("instruction_overlays"):
        op.create_table(
            "instruction_overlays",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_username", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("prompt_content", sa.Text(), nullable=False),
            sa.Column("overlay_metadata", sa.JSON(), nullable=True),
            sa.Column("scope", sa.String(), nullable=False, server_default="task"),
            sa.Column("attachment_kind", sa.String(), nullable=True),
            sa.Column("attachment_id", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("expires_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["owner_username"], ["users.username"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_username", "name", name="uq_instruction_overlays_owner_name"),
        )
    _create_index_if_missing(
        "instruction_overlays",
        "ix_instruction_overlays_owner_username",
        ["owner_username"],
    )
    _create_index_if_missing(
        "instruction_overlays",
        "ix_instruction_overlays_attachment_kind",
        ["attachment_kind"],
    )
    _create_index_if_missing(
        "instruction_overlays",
        "ix_instruction_overlays_attachment_id",
        ["attachment_id"],
    )
    _create_index_if_missing(
        "instruction_overlays",
        "ix_instruction_overlays_attachment",
        ["owner_username", "attachment_kind", "attachment_id"],
    )


def downgrade() -> None:
    if _table_exists("instruction_overlays"):
        op.drop_table("instruction_overlays")
    if _table_exists("user_instruction_profiles"):
        op.drop_table("user_instruction_profiles")

