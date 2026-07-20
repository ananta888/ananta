"""Add Hub-owned semantic media contracts, membership and fenced leases.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-07-19 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_session_memberships" not in existing:
        op.create_table(
            "semantic_session_memberships",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=True),
            sa.Column("member_subject", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("permissions", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "session_id", "member_subject", "epoch",
                name="uq_semantic_membership_scope_epoch",
            ),
        )
        op.create_index(
            "ix_semantic_membership_scope", "semantic_session_memberships",
            ["tenant_id", "session_id", "epoch", "status"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_compute_contracts" not in existing:
        op.create_table(
            "semantic_compute_contracts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=True),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("digest", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("profile", sa.String(), nullable=False),
            sa.Column("security_mode", sa.String(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("contract_payload", sa.JSON(), nullable=False),
            sa.Column("active_scope_key", sa.String(), nullable=True),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "session_id", "epoch", "revision",
                name="uq_semantic_contract_scope_revision",
            ),
            sa.UniqueConstraint("active_scope_key", name="uq_semantic_contract_active_scope"),
        )
        op.create_index(
            "ix_semantic_contract_owner_scope", "semantic_compute_contracts",
            ["tenant_id", "owner_subject", "session_id"],
        )
        op.create_index(
            "ix_semantic_contract_scope_status", "semantic_compute_contracts",
            ["tenant_id", "session_id", "epoch", "status"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_contract_mutations" not in existing:
        op.create_table(
            "semantic_contract_mutations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("contract_id", sa.String(), nullable=False),
            sa.Column("result_revision", sa.Integer(), nullable=False),
            sa.Column("result_status", sa.String(), nullable=False),
            sa.Column("result_digest", sa.String(), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "owner_subject", "operation", "idempotency_key_digest",
                name="uq_semantic_mutation_idempotency",
            ),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_lease_fences" not in existing:
        op.create_table(
            "semantic_lease_fences",
            sa.Column("scope_key", sa.String(), primary_key=True),
            sa.Column("last_token", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "semantic_compute_leases" not in existing:
        op.create_table(
            "semantic_compute_leases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("contract_id", sa.String(), nullable=False),
            sa.Column("contract_digest", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("task_type", sa.String(), nullable=False),
            sa.Column("audience", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("executor_id", sa.String(), nullable=False),
            sa.Column("sequence_start", sa.Integer(), nullable=False),
            sa.Column("sequence_end", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("resource_budget", sa.JSON(), nullable=False),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("active_scope_key", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("issued_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("deadline_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("active_scope_key", name="uq_semantic_lease_active_scope"),
            sa.UniqueConstraint("scope_key", "fencing_token", name="uq_semantic_lease_fence"),
        )
        op.create_index(
            "ix_semantic_lease_scope_status", "semantic_compute_leases",
            ["tenant_id", "session_id", "epoch", "status"],
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "semantic_compute_leases",
        "semantic_lease_fences",
        "semantic_contract_mutations",
        "semantic_compute_contracts",
        "semantic_session_memberships",
    ):
        if table in existing:
            op.drop_table(table)
