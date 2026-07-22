"""Add durable Hub-owned SFU broadcast rollout flags.

Revision ID: f7b8c9d0e1f2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "f7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_broadcast_feature_flags" not in existing:
        op.create_table(
            "sfu_broadcast_feature_flags",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("room_cohort", sa.String(), nullable=False),
            sa.Column("flag", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("rollout_stage", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("audited_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "region",
                "room_cohort",
                "flag",
                name="uq_sfu_broadcast_flag_scope",
            ),
        )
        for column in (
            "tenant_id",
            "region",
            "room_cohort",
            "flag",
            "version",
            "idempotency_key_digest",
            "audited_at",
        ):
            op.create_index(
                f"ix_sfu_broadcast_feature_flags_{column}",
                "sfu_broadcast_feature_flags",
                [column],
            )
        op.create_index(
            "ix_sfu_broadcast_flag_scope_version",
            "sfu_broadcast_feature_flags",
            ["tenant_id", "region", "room_cohort", "flag", "version"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_broadcast_feature_flag_mutations" not in existing:
        op.create_table(
            "sfu_broadcast_feature_flag_mutations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("feature_flag_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("room_cohort", sa.String(), nullable=False),
            sa.Column("flag", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("rollout_stage", sa.String(), nullable=False),
            sa.Column("expected_version", sa.Integer(), nullable=False),
            sa.Column("result_version", sa.Integer(), nullable=False),
            sa.Column("result_status", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("audited_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "idempotency_key_digest",
                name="uq_sfu_broadcast_flag_mutation_idempotency",
            ),
        )
        for column in (
            "feature_flag_id",
            "tenant_id",
            "region",
            "room_cohort",
            "flag",
            "result_version",
            "actor",
            "idempotency_key_digest",
            "audited_at",
        ):
            op.create_index(
                f"ix_sfu_broadcast_feature_flag_mutations_{column}",
                "sfu_broadcast_feature_flag_mutations",
                [column],
            )
        op.create_index(
            "ix_sfu_broadcast_flag_mutation_scope",
            "sfu_broadcast_feature_flag_mutations",
            ["tenant_id", "region", "room_cohort", "flag", "result_version"],
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_broadcast_feature_flag_mutations" in existing:
        op.drop_table("sfu_broadcast_feature_flag_mutations")
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_broadcast_feature_flags" in existing:
        op.drop_table("sfu_broadcast_feature_flags")
