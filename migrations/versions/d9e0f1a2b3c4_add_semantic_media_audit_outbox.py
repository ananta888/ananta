"""Add the semantic-media transactional audit outbox.

Revision ID: d9e0f1a2b3c4
Revises: a9b0c1d2e3f4
Create Date: 2026-07-19 23:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "semantic_media_audit_outbox" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "semantic_media_audit_outbox",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("idempotency_digest", sa.String(), nullable=False),
        sa.Column("tenant_digest", sa.String(), nullable=False),
        sa.Column("scope_digest", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("transition", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("contract_ref", sa.String(), nullable=True),
        sa.Column("lease_ref", sa.String(), nullable=True),
        sa.Column("job_ref", sa.String(), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("available_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_digest",
            name="uq_semantic_media_audit_outbox_idempotency",
        ),
    )
    for column in (
        "event_id",
        "idempotency_digest",
        "tenant_digest",
        "scope_digest",
        "event_type",
        "epoch",
        "contract_ref",
        "lease_ref",
        "job_ref",
        "created_at_ms",
        "expires_at_ms",
        "available_at_ms",
    ):
        op.create_index(
            f"ix_semantic_media_audit_outbox_{column}",
            "semantic_media_audit_outbox",
            [column],
        )
    op.create_index(
        "ix_semantic_media_audit_outbox_dispatch",
        "semantic_media_audit_outbox",
        ["available_at_ms", "created_at_ms", "id"],
    )
    op.create_index(
        "ix_semantic_media_audit_outbox_scope",
        "semantic_media_audit_outbox",
        ["tenant_digest", "scope_digest", "expires_at_ms"],
    )


def downgrade() -> None:
    if "semantic_media_audit_outbox" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("semantic_media_audit_outbox")
