"""Add Voice deletion tombstones and generic cleanup actions.

Revision ID: l1m2n3o4p5q6
Revises: k1l2m3n4o5p6
Create Date: 2026-07-12 00:00:03.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "l1m2n3o4p5q6"
down_revision: str | Sequence[str] | None = "k1l2m3n4o5p6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "voice_deletion_tombstones" not in tables:
        op.create_table(
            "voice_deletion_tombstones",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scope_digest", sa.String(), nullable=False),
            sa.Column("key_version", sa.String(), nullable=False, server_default="hub-hmac-sha256-v1"),
            sa.Column("idempotency_key_digests", sa.JSON(), nullable=True, server_default="[]"),
            sa.Column("deleted_at", sa.Float(), nullable=False),
            sa.Column("reconciliation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("last_reconciled_at", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scope_digest",
                name="uq_voice_deletion_tombstone_scope_digest",
            ),
        )
        for index_name, columns in (
            ("ix_voice_deletion_tombstones_scope_digest", ["scope_digest"]),
            ("ix_voice_deletion_tombstones_deleted_at", ["deleted_at"]),
        ):
            op.create_index(index_name, "voice_deletion_tombstones", columns)

    cleanup_columns = {
        column["name"]: column for column in inspect(op.get_bind()).get_columns("voice_runtime_cleanups")
    }
    if "cleanup_kind" not in cleanup_columns:
        with op.batch_alter_table("voice_runtime_cleanups") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "cleanup_kind",
                    sa.String(),
                    nullable=False,
                    server_default="runtime_stream_delete",
                )
            )
            batch_op.create_index(
                "ix_voice_runtime_cleanups_cleanup_kind",
                ["cleanup_kind"],
                unique=False,
            )
    cleanup_columns = {
        column["name"]: column for column in inspect(op.get_bind()).get_columns("voice_runtime_cleanups")
    }
    with op.batch_alter_table("voice_runtime_cleanups") as batch_op:
        if not cleanup_columns["runtime_session_ciphertext"].get("nullable"):
            batch_op.alter_column(
                "runtime_session_ciphertext",
                existing_type=sa.String(),
                nullable=True,
            )
        if not cleanup_columns["target_digest"].get("nullable"):
            batch_op.alter_column(
                "target_digest",
                existing_type=sa.String(),
                nullable=True,
            )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "voice_runtime_cleanups" in tables:
        columns = {column["name"] for column in inspect(op.get_bind()).get_columns("voice_runtime_cleanups")}
        if "cleanup_kind" in columns:
            op.execute(
                sa.text(
                    "DELETE FROM voice_runtime_cleanups "
                    "WHERE cleanup_kind != 'runtime_stream_delete'"
                )
            )
            with op.batch_alter_table("voice_runtime_cleanups") as batch_op:
                batch_op.alter_column(
                    "runtime_session_ciphertext",
                    existing_type=sa.String(),
                    nullable=False,
                )
                batch_op.alter_column(
                    "target_digest",
                    existing_type=sa.String(),
                    nullable=False,
                )
                batch_op.drop_index("ix_voice_runtime_cleanups_cleanup_kind")
                batch_op.drop_column("cleanup_kind")
    if "voice_deletion_tombstones" in tables:
        op.drop_table("voice_deletion_tombstones")
