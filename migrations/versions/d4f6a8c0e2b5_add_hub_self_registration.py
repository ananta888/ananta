"""Add Hub self-registration verification and approval state.

Revision ID: d4f6a8c0e2b5
Revises: c3e5a7b9d1f4
Create Date: 2026-08-22
"""
from __future__ import annotations

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d4f6a8c0e2b5"
down_revision: str | Sequence[str] | None = "c3e5a7b9d1f4"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("email", sa.String(), nullable=True),
    sa.Column("email_verified_at", sa.Float(), nullable=True),
    sa.Column("admin_approved_at", sa.Float(), nullable=True),
    sa.Column("registration_requires_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("email_verification_token_hash", sa.String(), nullable=True),
    sa.Column("email_verification_expires_at", sa.Float(), nullable=True),
)


def upgrade() -> None:
    if "users" not in set(inspect(op.get_bind()).get_table_names()):
        return
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("users", column)
    op.create_index("ix_users_email", "users", ["email"], unique=True, if_not_exists=True)
    op.create_index(
        "ix_users_email_verification_token_hash", "users", ["email_verification_token_hash"],
        unique=True, if_not_exists=True,
    )


def downgrade() -> None:
    if "users" not in set(inspect(op.get_bind()).get_table_names()):
        return
    for name in ("ix_users_email_verification_token_hash", "ix_users_email"):
        op.drop_index(name, table_name="users", if_exists=True)
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    for column in reversed(_COLUMNS):
        if column.name in existing:
            op.drop_column("users", column.name)
