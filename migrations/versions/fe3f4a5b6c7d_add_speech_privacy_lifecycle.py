"""Add content-free speech privacy lifecycle completion markers.

Revision ID: fe3f4a5b6c7d
Revises: fd2e3f4a5b6c
Create Date: 2026-07-20 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "fe3f4a5b6c7d"
down_revision: str | Sequence[str] | None = "fd2e3f4a5b6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "speech_privacy_lifecycles" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "speech_privacy_lifecycles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("revocation_epoch", sa.Integer(), nullable=False),
        sa.Column("safe_state", sa.String(length=64), nullable=False),
        sa.Column("local_fenced", sa.Boolean(), nullable=False),
        sa.Column("key_destroyed", sa.Boolean(), nullable=False),
        sa.Column("remote_state", sa.String(length=32), nullable=False),
        sa.Column("remote_request_digest", sa.String(length=64), nullable=True),
        sa.Column("remote_ack_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "evidence_digest",
            name="uq_speech_privacy_lifecycle_evidence",
        ),
    )
    for name in (
        "tenant_id",
        "owner_subject",
        "scope_digest",
        "evidence_digest",
        "phase",
        "safe_state",
        "remote_state",
        "remote_request_digest",
        "remote_ack_digest",
    ):
        op.create_index(
            f"ix_speech_privacy_lifecycles_{name}",
            "speech_privacy_lifecycles",
            [name],
        )


def downgrade() -> None:
    if "speech_privacy_lifecycles" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("speech_privacy_lifecycles")
