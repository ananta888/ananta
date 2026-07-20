"""Persist bounded WebRTC nonce digests with replay windows.

Revision ID: fd2e3f4a5b6c
Revises: fc1d2e3f4a5b
Create Date: 2026-07-20 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "fd2e3f4a5b6c"
down_revision: str | Sequence[str] | None = "fc1d2e3f4a5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "webrtc_replay_states" not in set(inspector.get_table_names()):
        op.create_table(
            "webrtc_replay_states",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("scope_key", sa.String(length=260), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("sender_id", sa.String(length=128), nullable=False),
            sa.Column("traffic_class", sa.String(length=16), nullable=False),
            sa.Column("highest_sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("accepted_sequences", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("accepted_nonce_digests", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        for name in ("scope_key", "epoch", "sender_id", "traffic_class", "expires_at", "updated_at"):
            op.create_index(f"ix_webrtc_replay_states_{name}", "webrtc_replay_states", [name])
        return
    columns = {column["name"] for column in inspector.get_columns("webrtc_replay_states")}
    if "accepted_nonce_digests" not in columns:
        with op.batch_alter_table("webrtc_replay_states") as batch:
            batch.add_column(
                sa.Column("accepted_nonce_digests", sa.JSON(), nullable=False, server_default="{}")
            )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "webrtc_replay_states" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("webrtc_replay_states")}
    if "accepted_nonce_digests" in columns:
        with op.batch_alter_table("webrtc_replay_states") as batch:
            batch.drop_column("accepted_nonce_digests")
