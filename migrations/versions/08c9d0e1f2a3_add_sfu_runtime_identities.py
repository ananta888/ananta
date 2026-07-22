"""Add durable SFU runtime identities and credential lifecycle.

Revision ID: 08c9d0e1f2a3
Revises: f7b8c9d0e1f2
Create Date: 2026-07-22 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "08c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "f7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_runtime_identities" not in existing:
        op.create_table(
            "sfu_runtime_identities",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("node_id", sa.String(), nullable=False),
            sa.Column("runtime_control_mode", sa.String(), nullable=False),
            sa.Column("roles", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("active_credential_id", sa.String(), nullable=False),
            sa.Column("previous_credential_id", sa.String(), nullable=True),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("enrolled_at", sa.Float(), nullable=False),
            sa.Column("rotated_at", sa.Float(), nullable=True),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("revocation_deadline_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("node_id", name="uq_sfu_runtime_identity_node"),
        )
        for column in (
            "node_id",
            "runtime_control_mode",
            "status",
            "version",
            "previous_credential_id",
            "actor",
            "revoked_at",
            "revocation_deadline_at",
        ):
            op.create_index(f"ix_sfu_runtime_identities_{column}", "sfu_runtime_identities", [column])
        op.create_index(
            "ix_sfu_runtime_identity_status_version",
            "sfu_runtime_identities",
            ["status", "version"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_runtime_credentials" not in existing:
        op.create_table(
            "sfu_runtime_credentials",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("identity_id", sa.String(), nullable=False),
            sa.Column("credential_kind", sa.String(), nullable=False),
            sa.Column("public_key_fingerprint", sa.String(), nullable=False),
            sa.Column("credential_fingerprint", sa.String(), nullable=False),
            sa.Column("proof_nonce_digest", sa.String(), nullable=False),
            sa.Column("certificate_serial", sa.String(), nullable=True),
            sa.Column("certificate_sans", sa.JSON(), nullable=False),
            sa.Column("certificate_ekus", sa.JSON(), nullable=False),
            sa.Column("certificate_not_before", sa.Float(), nullable=True),
            sa.Column("certificate_not_after", sa.Float(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("valid_from", sa.Float(), nullable=False),
            sa.Column("overlap_until", sa.Float(), nullable=True),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["identity_id"], ["sfu_runtime_identities.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("credential_fingerprint", name="uq_sfu_runtime_credential_fingerprint"),
            sa.UniqueConstraint("public_key_fingerprint", name="uq_sfu_runtime_public_key_fingerprint"),
            sa.UniqueConstraint("proof_nonce_digest", name="uq_sfu_runtime_proof_nonce"),
        )
        for column in (
            "identity_id",
            "credential_kind",
            "public_key_fingerprint",
            "credential_fingerprint",
            "proof_nonce_digest",
            "certificate_serial",
            "status",
            "overlap_until",
            "revoked_at",
        ):
            op.create_index(f"ix_sfu_runtime_credentials_{column}", "sfu_runtime_credentials", [column])
        op.create_index(
            "ix_sfu_runtime_credential_identity_status",
            "sfu_runtime_credentials",
            ["identity_id", "status"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_runtime_identity_mutations" not in existing:
        op.create_table(
            "sfu_runtime_identity_mutations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("identity_id", sa.String(), nullable=False),
            sa.Column("node_id", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("expected_version", sa.Integer(), nullable=False),
            sa.Column("result_version", sa.Integer(), nullable=False),
            sa.Column("result_status", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("audited_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "actor", "idempotency_key_digest", name="uq_sfu_runtime_mutation_actor_idempotency"
            ),
        )
        for column in (
            "identity_id",
            "node_id",
            "operation",
            "result_version",
            "actor",
            "idempotency_key_digest",
            "audited_at",
        ):
            op.create_index(f"ix_sfu_runtime_identity_mutations_{column}", "sfu_runtime_identity_mutations", [column])
        op.create_index(
            "ix_sfu_runtime_mutation_node_version",
            "sfu_runtime_identity_mutations",
            ["node_id", "result_version"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_runtime_enrollment_rate_limits" not in existing:
        op.create_table(
            "sfu_runtime_enrollment_rate_limits",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("source_digest", sa.String(), nullable=False),
            sa.Column("window_started_at", sa.Integer(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "actor", "source_digest", "window_started_at", name="uq_sfu_runtime_enrollment_bucket"
            ),
        )
        for column in ("actor", "source_digest", "window_started_at"):
            op.create_index(
                f"ix_sfu_runtime_enrollment_rate_limits_{column}",
                "sfu_runtime_enrollment_rate_limits",
                [column],
            )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "sfu_runtime_enrollment_rate_limits",
        "sfu_runtime_identity_mutations",
        "sfu_runtime_credentials",
        "sfu_runtime_identities",
    ):
        if table in existing:
            op.drop_table(table)
