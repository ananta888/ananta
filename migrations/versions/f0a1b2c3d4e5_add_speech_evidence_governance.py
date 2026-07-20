"""Add governed speech evidence, lineage and cleanup persistence.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-07-19 14:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_consents" not in existing:
        op.create_table(
            "speech_evidence_consents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("speaker_id", sa.String(), nullable=False),
            sa.Column("recipient_id", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("session_epoch", sa.Integer(), nullable=False),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("purpose", sa.String(), nullable=False),
            sa.Column("scope_digest", sa.String(length=64), nullable=False),
            sa.Column("consent_digest", sa.String(length=64), nullable=False),
            sa.Column("scope_payload", sa.JSON(), nullable=False),
            sa.Column("required_signers", sa.JSON(), nullable=False),
            sa.Column("signature_digests", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("issued_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index(
            "ix_speech_evidence_consents_scope",
            "speech_evidence_consents",
            ["tenant_id", "owner_subject", "pair_id", "session_id"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_keys" not in existing:
        op.create_table(
            "speech_evidence_keys",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("purpose", sa.String(), nullable=False),
            sa.Column("artifact_class", sa.String(), nullable=False),
            sa.Column("artifact_ref", sa.String(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("wrapping_epoch", sa.Integer(), nullable=False),
            sa.Column("wrapping_algorithm", sa.String(), nullable=False),
            sa.Column("wrapped_dek", sa.LargeBinary(), nullable=True),
            sa.Column("wrapping_nonce", sa.LargeBinary(), nullable=True),
            sa.Column("destroyed_at_ms", sa.BigInteger(), nullable=True),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("rotated_at_ms", sa.BigInteger(), nullable=True),
            sa.UniqueConstraint("tenant_id", "artifact_ref", name="uq_speech_evidence_key_artifact"),
        )
        op.create_index(
            "ix_speech_evidence_keys_scope",
            "speech_evidence_keys",
            ["tenant_id", "pair_id", "purpose", "key_epoch"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence" not in existing:
        op.create_table(
            "speech_evidence",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("session_epoch", sa.Integer(), nullable=False),
            sa.Column("speaker_scope_digest", sa.String(length=64), nullable=False),
            sa.Column("utterance_family_id", sa.String(), nullable=False),
            sa.Column("evidence_class", sa.String(), nullable=False),
            sa.Column("purpose", sa.String(), nullable=False),
            sa.Column("consent_id", sa.String(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("content_digest", sa.String(length=64), nullable=False),
            sa.Column("cipher_content_digest", sa.String(length=64), nullable=False),
            sa.Column("source_digest", sa.String(length=64), nullable=False),
            sa.Column("provenance_digest", sa.String(length=64), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("nonce", sa.LargeBinary(), nullable=False),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("byte_count", sa.Integer(), nullable=False),
            sa.Column("retention_seconds", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("admission_digest", sa.String(length=64), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "pair_id",
                "session_id",
                "evidence_class",
                "content_digest",
                name="uq_speech_evidence_scoped_digest",
            ),
        )
        op.create_index("ix_speech_evidence_expiry", "speech_evidence", ["state", "expires_at_ms"])
        op.create_index(
            "ix_speech_evidence_scope", "speech_evidence", ["tenant_id", "owner_subject", "pair_id", "session_id"]
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_admissions" not in existing:
        op.create_table(
            "speech_evidence_admissions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("evidence_id", sa.String(), nullable=False),
            sa.Column("evidence_digest", sa.String(length=64), nullable=False),
            sa.Column("admission_digest", sa.String(length=64), nullable=False, unique=True),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("decision", sa.String(), nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("evidence_id", "policy_version", name="uq_speech_admission_evidence_policy"),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_curation_tasks" not in existing:
        op.create_table(
            "speech_curation_tasks",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("parent_task_id", sa.String(), nullable=False),
            sa.Column("admission_digest", sa.String(length=64), nullable=False),
            sa.Column("evidence_refs", sa.JSON(), nullable=False),
            sa.Column("consent_id", sa.String(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("task_binding", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("result_artifact_ref", sa.String(), nullable=True),
            sa.Column("result_artifact_digest", sa.String(length=64), nullable=True),
            sa.Column("deadline_epoch_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("tenant_id", "admission_digest", name="uq_speech_curation_admission"),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_dataset_manifests" not in existing:
        op.create_table(
            "speech_dataset_manifests",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("version", sa.String(), nullable=False),
            sa.Column("parent_digest", sa.String(length=64), nullable=True),
            sa.Column("manifest_digest", sa.String(length=64), nullable=False),
            sa.Column("manifest_payload", sa.JSON(), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("consent_refs", sa.JSON(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "dataset_id",
                "version",
                name="uq_speech_dataset_manifest_version",
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "manifest_digest",
                name="uq_speech_dataset_manifest_digest",
            ),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_lineage_nodes" not in existing:
        op.create_table(
            "speech_lineage_nodes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("digest", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("consent_id", sa.String(), nullable=True),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("tenant_id", "owner_subject", "digest", "kind", name="uq_speech_lineage_node"),
        )
        op.create_index(
            "ix_speech_lineage_node_scope", "speech_lineage_nodes", ["tenant_id", "owner_subject", "status"]
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_lineage_edges" not in existing:
        op.create_table(
            "speech_lineage_edges",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("relation", sa.String(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("tenant_id", "source_id", "target_id", "relation", name="uq_speech_lineage_edge"),
        )
        op.create_index("ix_speech_lineage_edge_forward", "speech_lineage_edges", ["tenant_id", "source_id"])
        op.create_index("ix_speech_lineage_edge_backward", "speech_lineage_edges", ["tenant_id", "target_id"])

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_lineage_outbox" not in existing:
        op.create_table(
            "speech_lineage_outbox",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("event_digest", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("tenant_id", "owner_subject", "event_digest", name="uq_speech_lineage_outbox_event"),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_revocations" not in existing:
        op.create_table(
            "speech_evidence_revocations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("evidence_digest", sa.String(length=64), nullable=False),
            sa.Column("consent_id", sa.String(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("impact_digest", sa.String(length=64), nullable=False),
            sa.Column("remote_state", sa.String(), nullable=False),
            sa.Column("remote_request_digest", sa.String(length=64), nullable=True),
            sa.Column("remote_ack_digest", sa.String(length=64), nullable=True),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("tenant_id", "evidence_digest", name="uq_speech_revocation_evidence"),
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_cleanups" not in existing:
        op.create_table(
            "speech_evidence_cleanups",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("evidence_id", sa.String(), nullable=False),
            sa.Column("evidence_digest", sa.String(length=64), nullable=False),
            sa.Column("consent_id", sa.String(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("impact_decision_digest", sa.String(length=64), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("artifact_cleaned", sa.Boolean(), nullable=False),
            sa.Column("key_destroyed", sa.Boolean(), nullable=False),
            sa.Column("ciphertext_deleted", sa.Boolean(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("last_reason_code", sa.String(), nullable=True),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("tenant_id", "evidence_id", name="uq_speech_cleanup_evidence"),
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "speech_evidence_cleanups",
        "speech_evidence_revocations",
        "speech_lineage_outbox",
        "speech_lineage_edges",
        "speech_lineage_nodes",
        "speech_dataset_manifests",
        "speech_curation_tasks",
        "speech_evidence_admissions",
        "speech_evidence",
        "speech_evidence_keys",
        "speech_evidence_consents",
    ):
        if table in existing:
            op.drop_table(table)
