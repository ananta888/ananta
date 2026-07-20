"""Add the shared, traffic-isolated semantic relay store.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-07-19 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_relay_cursors" not in existing:
        op.create_table(
            "semantic_relay_cursors",
            sa.Column("scope_key", sa.String(length=390), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("session_id", sa.String(length=128), nullable=False),
            sa.Column("audience_id", sa.String(length=128), nullable=False),
            sa.Column("traffic_class", sa.String(length=32), nullable=False),
            sa.Column("next_cursor", sa.Integer(), nullable=False),
            sa.Column("acknowledged_cursor", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index(
            "ix_semantic_relay_cursor_scope",
            "semantic_relay_cursors",
            ["tenant_id", "session_id", "audience_id", "traffic_class"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_relay_envelopes" not in existing:
        op.create_table(
            "semantic_relay_envelopes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("message_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("session_id", sa.String(length=128), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("sender_id", sa.String(length=128), nullable=False),
            sa.Column("audience_id", sa.String(length=128), nullable=False),
            sa.Column("traffic_class", sa.String(length=32), nullable=False),
            sa.Column("payload_bytes", sa.Integer(), nullable=False),
            sa.Column("payload_digest", sa.String(length=64), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "session_id",
                "audience_id",
                "message_id",
                name="uq_semantic_relay_message_audience",
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "session_id",
                "audience_id",
                "traffic_class",
                "cursor",
                name="uq_semantic_relay_audience_cursor",
            ),
        )
        op.create_index(
            "ix_semantic_relay_delivery",
            "semantic_relay_envelopes",
            ["tenant_id", "session_id", "audience_id", "traffic_class", "cursor"],
        )
        op.create_index(
            "ix_semantic_relay_expiry",
            "semantic_relay_envelopes",
            ["expires_at"],
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_relay_envelopes" in existing:
        op.drop_table("semantic_relay_envelopes")
    if "semantic_relay_cursors" in existing:
        op.drop_table("semantic_relay_cursors")
