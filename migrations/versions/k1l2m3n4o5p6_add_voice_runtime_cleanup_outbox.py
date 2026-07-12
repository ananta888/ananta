"""Add durable Voice runtime cleanup outbox.

Revision ID: k1l2m3n4o5p6
Revises: j1k2l3m4n5o6
Create Date: 2026-07-12 00:00:02.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "k1l2m3n4o5p6"
down_revision: str | Sequence[str] | None = "j1k2l3m4n5o6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "voice_runtime_cleanups" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "voice_runtime_cleanups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("source_session_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("runtime_session_ciphertext", sa.String(), nullable=False),
        sa.Column("target_digest", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "profile_id",
            "source_session_id",
            name="uq_voice_runtime_cleanup_scope_session",
        ),
    )
    for index_name, columns in (
        ("ix_voice_runtime_cleanups_tenant_id", ["tenant_id"]),
        ("ix_voice_runtime_cleanups_owner_subject", ["owner_subject"]),
        ("ix_voice_runtime_cleanups_profile_id", ["profile_id"]),
        ("ix_voice_runtime_cleanups_source_session_id", ["source_session_id"]),
        ("ix_voice_runtime_cleanups_operation", ["operation"]),
        ("ix_voice_runtime_cleanups_state", ["state"]),
        (
            "ix_voice_runtime_cleanups_scope_profile",
            ["tenant_id", "owner_subject", "profile_id"],
        ),
    ):
        op.create_index(index_name, "voice_runtime_cleanups", columns)


def downgrade() -> None:
    if "voice_runtime_cleanups" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("voice_runtime_cleanups")
