"""Persist Hub-owned semantic SFU admission state.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-19 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_sfu_room_states" not in existing:
        op.create_table(
            "semantic_sfu_room_states",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("participants", sa.JSON(), nullable=False),
            sa.Column("publications", sa.JSON(), nullable=False),
            sa.Column("subscriptions", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "session_id", name="uq_semantic_sfu_room_scope"),
        )
        for column in ("tenant_id", "session_id", "revision", "updated_at"):
            op.create_index(f"ix_semantic_sfu_room_states_{column}", "semantic_sfu_room_states", [column])
        op.create_index(
            "ix_semantic_sfu_room_scope_revision",
            "semantic_sfu_room_states",
            ["tenant_id", "session_id", "revision"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_sfu_admission_receipts" not in existing:
        op.create_table(
            "semantic_sfu_admission_receipts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("actor_id", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "session_id",
                "actor_id",
                "operation",
                "idempotency_key_digest",
                name="uq_semantic_sfu_receipt_idempotency",
            ),
        )
        for column in (
            "tenant_id",
            "session_id",
            "actor_id",
            "operation",
            "idempotency_key_digest",
            "expires_at",
        ):
            op.create_index(
                f"ix_semantic_sfu_admission_receipts_{column}",
                "semantic_sfu_admission_receipts",
                [column],
            )
        op.create_index(
            "ix_semantic_sfu_receipt_expiry",
            "semantic_sfu_admission_receipts",
            ["expires_at", "created_at"],
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_sfu_admission_receipts" in existing:
        op.drop_table("semantic_sfu_admission_receipts")
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_sfu_room_states" in existing:
        op.drop_table("semantic_sfu_room_states")
