"""Add persistent speech reconciliation control plane.

Revision ID: a2b3c4d5e6f7
Revises: f0a1b2c3d4e5
Create Date: 2026-07-19 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "speech_reconciliation_jobs" not in _tables():
        op.create_table(
            "speech_reconciliation_jobs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("pair_scope_digest", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("consent_id", sa.String(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("input_manifest_digest", sa.String(), nullable=False),
            sa.Column("input_lineage_digest", sa.String(), nullable=False),
            sa.Column("input_artifact_ref", sa.String(), nullable=False),
            sa.Column("policy_digest", sa.String(), nullable=False),
            sa.Column("research_policy_ref", sa.String(), nullable=True),
            sa.Column("budget_plan", sa.JSON(), nullable=False),
            sa.Column("source_duration_ms", sa.BigInteger(), nullable=False),
            sa.Column("max_compute_factor", sa.Integer(), nullable=False),
            sa.Column("ledger_sequence", sa.BigInteger(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("deadline_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("active_attempt_id", sa.String(), nullable=True),
            sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
            sa.Column("checkpoint_count", sa.Integer(), nullable=False),
            sa.Column("resolved_count", sa.Integer(), nullable=False),
            sa.Column("unresolved_count", sa.Integer(), nullable=False),
            sa.Column("rejected_count", sa.Integer(), nullable=False),
            sa.Column("quarantined_count", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("finished_at_ms", sa.BigInteger(), nullable=True),
            sa.UniqueConstraint(
                "tenant_id", "owner_subject", "idempotency_key_digest", name="uq_speech_reconciliation_job_idempotency"
            ),
        )
        op.create_index(
            "ix_speech_reconciliation_job_scope_created",
            "speech_reconciliation_jobs",
            ["tenant_id", "owner_subject", "created_at_ms"],
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "pair_scope_digest",
            "idempotency_key_digest",
            "request_digest",
            "state",
            "stage",
            "consent_id",
            "input_manifest_digest",
            "input_lineage_digest",
            "policy_digest",
            "deadline_at_ms",
            "active_attempt_id",
        ):
            op.create_index(f"ix_speech_reconciliation_jobs_{column}", "speech_reconciliation_jobs", [column])

    if "speech_reconciliation_attempts" not in _tables():
        op.create_table(
            "speech_reconciliation_attempts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), sa.ForeignKey("speech_reconciliation_jobs.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("worker_id_digest", sa.String(), nullable=False),
            sa.Column("worker_capability_digest", sa.String(), nullable=False),
            sa.Column("location_digest", sa.String(), nullable=False),
            sa.Column("resource_profile_digest", sa.String(), nullable=False),
            sa.Column("fencing_token_digest", sa.String(), nullable=False),
            sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
            sa.Column("lease_expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("deadline_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("last_heartbeat_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("checkpoint_sequence", sa.Integer(), nullable=False),
            sa.Column("checkpoint_digest", sa.String(), nullable=True),
            sa.Column("checkpoint_ref", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("finished_at_ms", sa.BigInteger(), nullable=True),
            sa.UniqueConstraint("job_id", "attempt_number", name="uq_speech_reconciliation_attempt_number"),
            sa.UniqueConstraint("job_id", "fencing_epoch", name="uq_speech_reconciliation_attempt_fence"),
        )
        for column in (
            "job_id",
            "tenant_id",
            "owner_subject",
            "state",
            "worker_id_digest",
            "fencing_token_digest",
            "fencing_epoch",
            "lease_expires_at_ms",
            "deadline_at_ms",
            "last_heartbeat_at_ms",
            "checkpoint_digest",
        ):
            op.create_index(f"ix_speech_reconciliation_attempts_{column}", "speech_reconciliation_attempts", [column])

    if "speech_reconciliation_budget_ledgers" not in _tables():
        op.create_table(
            "speech_reconciliation_budget_ledgers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), sa.ForeignKey("speech_reconciliation_jobs.id"), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
            sa.Column("sequence", sa.BigInteger(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("source_duration_ms", sa.BigInteger(), nullable=False),
            sa.Column("compute_factor", sa.Integer(), nullable=False),
            sa.Column("allocated", sa.JSON(), nullable=False),
            sa.Column("reserved", sa.JSON(), nullable=False),
            sa.Column("consumed", sa.JSON(), nullable=False),
            sa.Column("remaining", sa.JSON(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("job_id", "sequence", name="uq_speech_reconciliation_ledger_sequence"),
        )
        for column in ("job_id", "attempt_id", "tenant_id", "owner_subject", "sequence", "stage"):
            op.create_index(
                f"ix_speech_reconciliation_budget_ledgers_{column}", "speech_reconciliation_budget_ledgers", [column]
            )

    if "speech_reconciliation_checkpoints" not in _tables():
        op.create_table(
            "speech_reconciliation_checkpoints",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), sa.ForeignKey("speech_reconciliation_jobs.id"), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("input_manifest_digest", sa.String(), nullable=False),
            sa.Column("policy_digest", sa.String(), nullable=False),
            sa.Column("ledger_sequence", sa.BigInteger(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("checkpoint_sequence", sa.Integer(), nullable=False),
            sa.Column("checkpoint_digest", sa.String(), nullable=False),
            sa.Column("checkpoint_ref", sa.String(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("state_digest", sa.String(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("job_id", "checkpoint_sequence", name="uq_speech_reconciliation_checkpoint_sequence"),
            sa.UniqueConstraint(
                "tenant_id", "owner_subject", "checkpoint_digest", name="uq_speech_reconciliation_checkpoint_digest"
            ),
        )
        for column in ("job_id", "attempt_id", "tenant_id", "owner_subject", "checkpoint_digest"):
            op.create_index(
                f"ix_speech_reconciliation_checkpoints_{column}", "speech_reconciliation_checkpoints", [column]
            )

    if "speech_reconciliation_artifacts" not in _tables():
        op.create_table(
            "speech_reconciliation_artifacts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), sa.ForeignKey("speech_reconciliation_jobs.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("artifact_kind", sa.String(), nullable=False),
            sa.Column("artifact_digest", sa.String(), nullable=False),
            sa.Column("artifact_ref", sa.String(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("revocation_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("job_id", "artifact_kind", "artifact_digest", name="uq_speech_reconciliation_artifact"),
        )
        for column in ("job_id", "tenant_id", "owner_subject", "artifact_kind", "artifact_digest"):
            op.create_index(f"ix_speech_reconciliation_artifacts_{column}", "speech_reconciliation_artifacts", [column])


def downgrade() -> None:
    existing = _tables()
    for table in (
        "speech_reconciliation_artifacts",
        "speech_reconciliation_checkpoints",
        "speech_reconciliation_budget_ledgers",
        "speech_reconciliation_attempts",
        "speech_reconciliation_jobs",
    ):
        if table in existing:
            op.drop_table(table)


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())
