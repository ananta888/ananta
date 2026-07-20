"""Add productive semantic-compute execution control state.

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-07-19 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "f8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_compute_candidate_keys" not in existing:
        op.create_table(
            "semantic_compute_candidate_keys",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("member_subject", sa.String(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("public_key_b64", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "session_id",
                "epoch",
                "member_subject",
                "key_id",
                name="uq_semantic_candidate_key_scope",
            ),
        )
        for column in ("tenant_id", "session_id", "epoch", "member_subject", "key_id", "status", "expires_at"):
            op.create_index(
                f"ix_semantic_compute_candidate_keys_{column}",
                "semantic_compute_candidate_keys",
                [column],
            )
        op.create_index(
            "ix_semantic_candidate_key_active",
            "semantic_compute_candidate_keys",
            ["tenant_id", "session_id", "epoch", "member_subject", "status", "expires_at"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_capability_advertisements" not in existing:
        op.create_table(
            "semantic_capability_advertisements",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=True),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("sender_subject", sa.String(), nullable=False),
            sa.Column("advertisement_id", sa.String(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("payload_digest", sa.String(), nullable=False),
            sa.Column("normalized_payload", sa.JSON(), nullable=False),
            sa.Column("observed_capacity", sa.Integer(), nullable=False),
            sa.Column("user_limit", sa.Integer(), nullable=False),
            sa.Column("reserve_capacity", sa.Integer(), nullable=False),
            sa.Column("recent_error_rate", sa.Float(), nullable=False),
            sa.Column("reputation", sa.Integer(), nullable=False),
            sa.Column("active_assignments", sa.Integer(), nullable=False),
            sa.Column("failure_domain", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "advertisement_id",
                name="uq_semantic_capability_advertisement",
            ),
        )
        for column in (
            "tenant_id",
            "session_id",
            "room_id",
            "epoch",
            "sender_subject",
            "advertisement_id",
            "key_id",
            "payload_digest",
            "status",
            "expires_at",
        ):
            op.create_index(
                f"ix_semantic_capability_advertisements_{column}",
                "semantic_capability_advertisements",
                [column],
            )
        op.create_index(
            "ix_semantic_capability_current",
            "semantic_capability_advertisements",
            ["tenant_id", "session_id", "epoch", "sender_subject", "status", "expires_at"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_compute_schedule_receipts" not in existing:
        op.create_table(
            "semantic_compute_schedule_receipts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("contract_id", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "contract_id",
                "idempotency_key_digest",
                name="uq_semantic_schedule_idempotency",
            ),
        )
        for column in ("tenant_id", "owner_subject", "contract_id", "idempotency_key_digest", "expires_at"):
            op.create_index(
                f"ix_semantic_compute_schedule_receipts_{column}",
                "semantic_compute_schedule_receipts",
                [column],
            )
        op.create_index(
            "ix_semantic_schedule_receipt_expiry",
            "semantic_compute_schedule_receipts",
            ["expires_at", "created_at"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_compute_lease_mutations" not in existing:
        op.create_table(
            "semantic_compute_lease_mutations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("lease_id", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("result_version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "lease_id",
                "operation",
                "idempotency_key_digest",
                name="uq_semantic_lease_mutation_idempotency",
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "lease_id",
            "operation",
            "idempotency_key_digest",
        ):
            op.create_index(
                f"ix_semantic_compute_lease_mutations_{column}",
                "semantic_compute_lease_mutations",
                [column],
            )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "semantic_compute_lease_mutations",
        "semantic_compute_schedule_receipts",
        "semantic_capability_advertisements",
        "semantic_compute_candidate_keys",
    ):
        if table in existing:
            op.drop_table(table)
