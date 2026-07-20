"""Add persistent content-free semantic-media audit events.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-19 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "semantic_media_audit_events" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "semantic_media_audit_events",
        sa.Column("id", sa.String(), primary_key=True),
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
        sa.UniqueConstraint(
            "idempotency_digest",
            name="uq_semantic_media_audit_idempotency",
        ),
    )
    for column in (
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
    ):
        op.create_index(
            f"ix_semantic_media_audit_events_{column}",
            "semantic_media_audit_events",
            [column],
        )
    op.create_index(
        "ix_semantic_media_audit_scope_page",
        "semantic_media_audit_events",
        ["tenant_digest", "scope_digest", "created_at_ms", "id"],
    )
    op.create_index(
        "ix_semantic_media_audit_expiry",
        "semantic_media_audit_events",
        ["expires_at_ms", "id"],
    )


def downgrade() -> None:
    if "semantic_media_audit_events" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("semantic_media_audit_events")
