"""Add replayable semantic relay wire metadata.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-07-19 14:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = inspect(op.get_bind())
    if "semantic_relay_envelopes" not in inspector.get_table_names():
        return set()
    return {str(column["name"]) for column in inspector.get_columns("semantic_relay_envelopes")}


def upgrade() -> None:
    columns = _columns()
    additions = (
        ("sequence", sa.Integer(), "1"),
        ("compression", sa.String(length=16), "'none'"),
        ("security_algorithm", sa.String(length=32), "'AES-GCM-256'"),
        ("key_id", sa.String(length=128), "'legacy-relay-key'"),
    )
    for name, column_type, default in additions:
        if name in columns:
            continue
        op.add_column(
            "semantic_relay_envelopes",
            sa.Column(name, column_type, nullable=False, server_default=sa.text(default)),
        )


def downgrade() -> None:
    columns = _columns()
    for name in ("key_id", "security_algorithm", "compression", "sequence"):
        if name in columns:
            op.drop_column("semantic_relay_envelopes", name)
