"""Add Hub-owned Voice configuration, review, consent and artifact tables.

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "i1j2k3l4m5n6"
down_revision: str | Sequence[str] | None = "h1i2j3k4l5m6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_table(
    name: str,
    *elements: sa.SchemaItem,
    indexes: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> None:
    if name in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(name, *elements)
    for index_name, columns in indexes:
        op.create_index(index_name, name, list(columns))


def upgrade() -> None:
    _create_table(
        "voice_configuration_deltas",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False, server_default=""),
        sa.Column("delta", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "scope",
            "scope_id",
            name="uq_voice_configuration_delta_scope",
        ),
        indexes=(
            ("ix_voice_configuration_deltas_tenant_id", ("tenant_id",)),
            ("ix_voice_configuration_deltas_owner_subject", ("owner_subject",)),
            ("ix_voice_configuration_deltas_scope", ("scope",)),
            ("ix_voice_configuration_deltas_scope_id", ("scope_id",)),
        ),
    )
    _create_table(
        "voice_consents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("categories", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="365"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("granted_at", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "owner_subject", "profile_id", name="uq_voice_consents_scope_profile"),
        indexes=(
            ("ix_voice_consents_tenant_id", ("tenant_id",)),
            ("ix_voice_consents_owner_subject", ("owner_subject",)),
            ("ix_voice_consents_profile_id", ("profile_id",)),
        ),
    )
    _create_table(
        "voice_reviews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("result_ref", sa.String(), nullable=False),
        sa.Column("candidate_ids", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("selected_candidate_id", sa.String(), nullable=True),
        sa.Column("correction_ciphertext", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        indexes=(
            ("ix_voice_reviews_tenant_id", ("tenant_id",)),
            ("ix_voice_reviews_owner_subject", ("owner_subject",)),
            ("ix_voice_reviews_profile_id", ("profile_id",)),
            ("ix_voice_reviews_session_id", ("session_id",)),
            ("ix_voice_reviews_result_ref", ("result_ref",)),
            ("ix_voice_reviews_state", ("state",)),
            ("ix_voice_reviews_scope_profile", ("tenant_id", "owner_subject", "profile_id")),
        ),
    )
    _create_table(
        "voice_personalization_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "profile_id",
            name="uq_voice_personalization_scope_profile",
        ),
        indexes=(
            ("ix_voice_personalization_profiles_tenant_id", ("tenant_id",)),
            ("ix_voice_personalization_profiles_owner_subject", ("owner_subject",)),
            ("ix_voice_personalization_profiles_profile_id", ("profile_id",)),
        ),
    )
    _create_table(
        "voice_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("consent_id", sa.String(), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("source_review_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source_ciphertext", sa.String(), nullable=True),
        sa.Column("target_ciphertext", sa.String(), nullable=True),
        sa.Column("feedback_metadata", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        indexes=(
            ("ix_voice_feedback_tenant_id", ("tenant_id",)),
            ("ix_voice_feedback_owner_subject", ("owner_subject",)),
            ("ix_voice_feedback_profile_id", ("profile_id",)),
            ("ix_voice_feedback_consent_id", ("consent_id",)),
            ("ix_voice_feedback_source_review_id", ("source_review_id",)),
            ("ix_voice_feedback_kind", ("kind",)),
            ("ix_voice_feedback_expires_at", ("expires_at",)),
            ("ix_voice_feedback_scope_profile", ("tenant_id", "owner_subject", "profile_id")),
        ),
    )
    feedback_columns = {column["name"] for column in inspect(op.get_bind()).get_columns("voice_feedback")}
    if "expires_at" not in feedback_columns:
        op.add_column(
            "voice_feedback",
            sa.Column("expires_at", sa.Float(), nullable=True, server_default="0"),
        )
        op.execute(
            sa.text(
                "UPDATE voice_feedback "
                "SET expires_at = created_at + :default_retention_seconds "
                "WHERE expires_at IS NULL OR expires_at = 0"
            ).bindparams(default_retention_seconds=365 * 86_400)
        )
        with op.batch_alter_table("voice_feedback") as batch_op:
            batch_op.alter_column("expires_at", existing_type=sa.Float(), nullable=False)
            batch_op.create_index("ix_voice_feedback_expires_at", ["expires_at"])
    _create_table(
        "voice_governance_idempotency",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("lease_expires_at", sa.Float(), nullable=False),
        sa.Column("result_metadata", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "operation",
            "idempotency_key",
            name="uq_voice_governance_idempotency_scope",
        ),
        indexes=(
            ("ix_voice_governance_idempotency_tenant_id", ("tenant_id",)),
            ("ix_voice_governance_idempotency_owner_subject", ("owner_subject",)),
            ("ix_voice_governance_idempotency_operation", ("operation",)),
            ("ix_voice_governance_idempotency_state", ("state",)),
            ("ix_voice_governance_idempotency_lease_expires_at", ("lease_expires_at",)),
        ),
    )
    _create_table(
        "voice_result_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False, server_default="default"),
        sa.Column("artifact_kind", sa.String(), nullable=False, server_default="result_envelope"),
        sa.Column("parent_artifact_id", sa.String(), nullable=True),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("payload_ciphertext", sa.String(), nullable=False),
        sa.Column("payload_digest", sa.String(), nullable=False),
        sa.Column("candidate_ids", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        indexes=(
            ("ix_voice_result_artifacts_tenant_id", ("tenant_id",)),
            ("ix_voice_result_artifacts_owner_subject", ("owner_subject",)),
            ("ix_voice_result_artifacts_profile_id", ("profile_id",)),
            ("ix_voice_result_artifacts_artifact_kind", ("artifact_kind",)),
            ("ix_voice_result_artifacts_parent_artifact_id", ("parent_artifact_id",)),
            ("ix_voice_result_artifacts_request_hash", ("request_hash",)),
            ("ix_voice_result_artifacts_expires_at", ("expires_at",)),
            ("ix_voice_result_artifacts_scope", ("tenant_id", "owner_subject", "created_at")),
        ),
    )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table_name in (
        "voice_result_artifacts",
        "voice_governance_idempotency",
        "voice_feedback",
        "voice_personalization_profiles",
        "voice_reviews",
        "voice_consents",
        "voice_configuration_deltas",
    ):
        if table_name in existing:
            op.drop_table(table_name)
