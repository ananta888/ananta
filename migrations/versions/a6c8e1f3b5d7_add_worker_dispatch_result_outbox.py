"""Repair pre-release Worker receipt tables missing result outbox fields.

Revision ID: a6c8e1f3b5d7
Revises: f4a7c9d2e1b3
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c8e1f3b5d7"
down_revision: str | Sequence[str] | None = "f4a7c9d2e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "knowledge_index_worker_dispatch_receipts"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        return set()
    return {
        column["name"] for column in inspector.get_columns(_TABLE)
    }


def upgrade() -> None:
    columns = _columns()
    if not columns:
        raise RuntimeError(
            "knowledge_index_worker_dispatch_receipt_table_missing"
        )
    additions = (
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default="claimed",
        ),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column(
            "completed_at_epoch_ms",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column(_TABLE, column)


def downgrade() -> None:
    # These fields are part of the canonical f4 receipt table.  This follow-up
    # revision exists only for pre-release environments that applied an older
    # local copy of f4, so downgrading to canonical f4 must retain them.
    return None
