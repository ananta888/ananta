"""Enforce feedback uniqueness and bounded Voice idempotency retention.

Revision ID: n1o2p3q4r5s6
Revises: m1n2o3p4q5r6
Create Date: 2026-07-12 00:00:05.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "n1o2p3q4r5s6"
down_revision: str | Sequence[str] | None = "m1n2o3p4q5r6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FEEDBACK_CONSTRAINT = "uq_voice_feedback_scope_source_kind"
_IDEMPOTENCY_EXPIRY_INDEX = "ix_voice_governance_idempotency_expires_at"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "voice_feedback" in tables:
        duplicate = bind.execute(
            sa.text(
                "SELECT tenant_id, owner_subject, profile_id, source_review_id, kind "
                "FROM voice_feedback "
                "GROUP BY tenant_id, owner_subject, profile_id, source_review_id, kind "
                "HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                "voice_feedback contains duplicate scoped source items; "
                "resolve them before applying the uniqueness migration"
            )
        unique_names = {
            item.get("name") for item in inspect(bind).get_unique_constraints("voice_feedback")
        }
        if _FEEDBACK_CONSTRAINT not in unique_names:
            with op.batch_alter_table("voice_feedback") as batch_op:
                batch_op.create_unique_constraint(
                    _FEEDBACK_CONSTRAINT,
                    ["tenant_id", "owner_subject", "profile_id", "source_review_id", "kind"],
                )

    if "voice_governance_idempotency" not in tables:
        return
    columns = {item["name"] for item in inspect(bind).get_columns("voice_governance_idempotency")}
    if "expires_at" not in columns:
        with op.batch_alter_table("voice_governance_idempotency") as batch_op:
            batch_op.add_column(sa.Column("expires_at", sa.Float(), nullable=True))
        bind.execute(
            sa.text(
                "UPDATE voice_governance_idempotency "
                "SET expires_at = COALESCE(updated_at, created_at, :now) + :ttl"
            ),
            {"now": 0.0, "ttl": 86_400.0},
        )
        with op.batch_alter_table("voice_governance_idempotency") as batch_op:
            batch_op.alter_column("expires_at", existing_type=sa.Float(), nullable=False)
    indexes = {item.get("name") for item in inspect(bind).get_indexes("voice_governance_idempotency")}
    if _IDEMPOTENCY_EXPIRY_INDEX not in indexes:
        op.create_index(
            _IDEMPOTENCY_EXPIRY_INDEX,
            "voice_governance_idempotency",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "voice_governance_idempotency" in tables:
        indexes = {
            item.get("name")
            for item in inspect(bind).get_indexes("voice_governance_idempotency")
        }
        if _IDEMPOTENCY_EXPIRY_INDEX in indexes:
            op.drop_index(
                _IDEMPOTENCY_EXPIRY_INDEX,
                table_name="voice_governance_idempotency",
            )
        columns = {
            item["name"]
            for item in inspect(bind).get_columns("voice_governance_idempotency")
        }
        if "expires_at" in columns:
            with op.batch_alter_table("voice_governance_idempotency") as batch_op:
                batch_op.drop_column("expires_at")
    if "voice_feedback" in tables:
        unique_names = {
            item.get("name") for item in inspect(bind).get_unique_constraints("voice_feedback")
        }
        if _FEEDBACK_CONSTRAINT in unique_names:
            with op.batch_alter_table("voice_feedback") as batch_op:
                batch_op.drop_constraint(_FEEDBACK_CONSTRAINT, type_="unique")
