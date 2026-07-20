"""Add persistent, revocable semantic-media capability grants.

Revision ID: eb1c2d3e4f5a
Revises: da0b1c2d3e4f
Create Date: 2026-07-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "eb1c2d3e4f5a"
down_revision: str | Sequence[str] | None = "da0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "semantic_media_capability_grants" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "semantic_media_capability_grants",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("subject_role", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("data_type", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("issuer", sa.String(length=16), nullable=False),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
        sa.Column("revocation_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "owner_id",
        "tenant_id",
        "subject_id",
        "subject_role",
        "capability",
        "scope_kind",
        "scope_id",
        "direction",
        "purpose",
        "epoch",
        "issued_at",
        "expires_at",
        "revoked_at",
        "revoked_by",
    ):
        op.create_index(
            f"ix_semantic_media_capability_grants_{column}",
            "semantic_media_capability_grants",
            [column],
        )
    op.create_index(
        "ix_semantic_capability_grant_subject_scope",
        "semantic_media_capability_grants",
        [
            "tenant_id",
            "subject_id",
            "scope_kind",
            "scope_id",
            "epoch",
            "capability",
            "revoked_at",
            "expires_at",
        ],
    )
    op.create_index(
        "ix_semantic_capability_grant_owner_scope",
        "semantic_media_capability_grants",
        ["tenant_id", "owner_id", "scope_kind", "scope_id", "epoch"],
    )


def downgrade() -> None:
    if "semantic_media_capability_grants" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("semantic_media_capability_grants")
