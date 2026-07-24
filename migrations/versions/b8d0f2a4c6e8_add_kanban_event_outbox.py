"""add durable Kanban event outbox and per-board sequence

Revision ID: b8d0f2a4c6e8
Revises: a7c9e1f3b5d7
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b8d0f2a4c6e8"
down_revision: str | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "kanban_board_sequences" not in tables:
        op.create_table(
            "kanban_board_sequences",
            sa.Column("board_id", sa.String(length=320), nullable=False),
            sa.Column(
                "last_sequence",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "last_sequence >= 0",
                name="ck_kanban_board_sequences_non_negative",
            ),
            sa.PrimaryKeyConstraint("board_id"),
        )
    if "kanban_event_outbox" not in tables:
        op.create_table(
            "kanban_event_outbox",
            sa.Column("board_id", sa.String(length=320), nullable=False),
            sa.Column("sequence", sa.BigInteger(), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.String(length=255), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=96), nullable=False),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("dedupe_key", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "sequence >= 1",
                name="ck_kanban_event_outbox_positive_sequence",
            ),
            sa.CheckConstraint(
                "revision >= 0",
                name="ck_kanban_event_outbox_non_negative_revision",
            ),
            sa.PrimaryKeyConstraint("board_id", "sequence"),
            sa.UniqueConstraint(
                "dedupe_key",
                name="uq_kanban_event_outbox_dedupe_key",
            ),
        )
        op.create_index(
            "ix_kanban_event_outbox_task_id",
            "kanban_event_outbox",
            ["task_id"],
        )


def downgrade() -> None:
    tables = _table_names()
    if "kanban_event_outbox" in tables:
        op.drop_table("kanban_event_outbox")
    if "kanban_board_sequences" in tables:
        op.drop_table("kanban_board_sequences")

