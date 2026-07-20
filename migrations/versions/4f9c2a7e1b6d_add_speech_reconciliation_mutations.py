"""Add durable idempotency receipts for Hub speech-job mutations.

Revision ID: 4f9c2a7e1b6d
Revises: ff4a5b6c7d8e
Create Date: 2026-07-20 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "4f9c2a7e1b6d"
down_revision: str | Sequence[str] | None = "ff4a5b6c7d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "speech_reconciliation_mutations" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "speech_reconciliation_mutations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column(
            "job_id",
            sa.String(),
            sa.ForeignKey("speech_reconciliation_jobs.id"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("result_job_version", sa.Integer(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("affected_attempt_id", sa.String(), nullable=True),
        sa.Column("affected_fencing_epoch", sa.BigInteger(), nullable=True),
        sa.Column("state_changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "job_id",
            "operation",
            "idempotency_key_digest",
            name="uq_speech_reconciliation_mutation_idempotency",
        ),
    )
    op.create_index(
        "ix_speech_reconciliation_mutation_scope_created",
        "speech_reconciliation_mutations",
        ["tenant_id", "owner_subject", "job_id", "created_at_ms"],
    )
    for column in (
        "tenant_id",
        "owner_subject",
        "job_id",
        "operation",
        "idempotency_key_digest",
        "request_digest",
        "created_at_ms",
    ):
        op.create_index(
            f"ix_speech_reconciliation_mutations_{column}",
            "speech_reconciliation_mutations",
            [column],
        )


def downgrade() -> None:
    if "speech_reconciliation_mutations" in inspect(op.get_bind()).get_table_names():
        op.drop_table("speech_reconciliation_mutations")
