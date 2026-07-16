"""Add durable Hub-owned ML-Intern LoRA training control-plane tables.

Revision ID: z4a5b6c7d8e9
Revises: y3z4a5b6c7d8
Create Date: 2026-07-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "z4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "y3z4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "ml_intern_datasets" not in existing:
        op.create_table(
            "ml_intern_datasets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("format_type", sa.String(), nullable=False),
            sa.Column("content_sha256", sa.String(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("train_record_count", sa.Integer(), nullable=False),
            sa.Column("validation_record_count", sa.Integer(), nullable=False),
            sa.Column("rejected_record_count", sa.Integer(), nullable=False),
            sa.Column("duplicate_record_count", sa.Integer(), nullable=False),
            sa.Column("secret_finding_count", sa.Integer(), nullable=False),
            sa.Column("storage_ref", sa.String(), nullable=False),
            sa.Column("train_storage_ref", sa.String(), nullable=True),
            sa.Column("validation_storage_ref", sa.String(), nullable=True),
            sa.Column("split_manifest", sa.JSON(), nullable=True),
            sa.Column("validation_report", sa.JSON(), nullable=True),
            sa.Column("dataset_metadata", sa.JSON(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "content_sha256",
                name="uq_ml_intern_dataset_scope_hash",
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "status",
            "format_type",
            "content_sha256",
            "created_at",
        ):
            op.create_index(
                f"ix_ml_intern_datasets_{column}",
                "ml_intern_datasets",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_ml_intern_dataset_scope_created",
            "ml_intern_datasets",
            ["tenant_id", "owner_subject", "created_at"],
            unique=False,
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "ml_intern_training_jobs" not in existing:
        op.create_table(
            "ml_intern_training_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=True),
            sa.Column("job_type", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("backend", sa.String(), nullable=False),
            sa.Column("base_model", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("phase", sa.String(), nullable=False),
            sa.Column("progress_percent", sa.Float(), nullable=False),
            sa.Column("current_step", sa.Integer(), nullable=True),
            sa.Column("max_steps", sa.Integer(), nullable=True),
            sa.Column("epoch", sa.Float(), nullable=True),
            sa.Column("train_loss", sa.Float(), nullable=True),
            sa.Column("eval_loss", sa.Float(), nullable=True),
            sa.Column("learning_rate", sa.Float(), nullable=True),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("request_spec", sa.JSON(), nullable=True),
            sa.Column("worker_job_id", sa.String(), nullable=True),
            sa.Column("active_attempt_id", sa.String(), nullable=True),
            sa.Column("queue_position", sa.Integer(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("checkpoint_ref", sa.String(), nullable=True),
            sa.Column("result_ref", sa.String(), nullable=True),
            sa.Column("result_summary", sa.JSON(), nullable=True),
            sa.Column("adapter_id", sa.String(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("retryable", sa.Boolean(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("started_at", sa.Float(), nullable=True),
            sa.Column("finished_at", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["dataset_id"], ["ml_intern_datasets.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "idempotency_key_digest",
                name="uq_ml_intern_training_job_scope_idempotency",
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "task_id",
            "dataset_id",
            "job_type",
            "mode",
            "backend",
            "base_model",
            "status",
            "phase",
            "idempotency_key_digest",
            "request_digest",
            "worker_job_id",
            "active_attempt_id",
            "checkpoint_ref",
            "result_ref",
            "adapter_id",
            "created_at",
        ):
            op.create_index(
                f"ix_ml_intern_training_jobs_{column}",
                "ml_intern_training_jobs",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_ml_intern_training_job_scope_created",
            "ml_intern_training_jobs",
            ["tenant_id", "owner_subject", "created_at"],
            unique=False,
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "ml_intern_training_capacity_leases" not in existing:
        op.create_table(
            "ml_intern_training_capacity_leases",
            sa.Column("slot", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["job_id"], ["ml_intern_training_jobs.id"]),
            sa.PrimaryKeyConstraint("slot"),
            sa.UniqueConstraint("job_id"),
        )
        op.create_index(
            "ix_ml_intern_training_capacity_leases_job_id",
            "ml_intern_training_capacity_leases",
            ["job_id"],
            unique=True,
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "ml_intern_training_execution_leases" not in existing:
        op.create_table(
            "ml_intern_training_execution_leases",
            sa.Column("slot", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("lease_expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["job_id"], ["ml_intern_training_jobs.id"]),
            sa.PrimaryKeyConstraint("slot"),
            sa.UniqueConstraint("job_id"),
        )
        op.create_index(
            "ix_ml_intern_training_execution_leases_job_id",
            "ml_intern_training_execution_leases",
            ["job_id"],
            unique=True,
        )
        op.create_index(
            "ix_ml_intern_training_execution_leases_lease_expires_at",
            "ml_intern_training_execution_leases",
            ["lease_expires_at"],
            unique=False,
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "ml_intern_training_attempts" not in existing:
        op.create_table(
            "ml_intern_training_attempts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("worker_id", sa.String(), nullable=False),
            sa.Column("worker_url", sa.String(), nullable=False),
            sa.Column("fencing_token_digest", sa.String(), nullable=False),
            sa.Column("lease_expires_at", sa.Float(), nullable=False),
            sa.Column("deadline_at", sa.Float(), nullable=False),
            sa.Column("last_heartbeat_at", sa.Float(), nullable=False),
            sa.Column("checkpoint_ref", sa.String(), nullable=True),
            sa.Column("result_ref", sa.String(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("finished_at", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["job_id"], ["ml_intern_training_jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "job_id",
                "attempt_number",
                name="uq_ml_intern_attempt_job_number",
            ),
        )
        for column in (
            "job_id",
            "tenant_id",
            "owner_subject",
            "status",
            "fencing_token_digest",
            "lease_expires_at",
            "deadline_at",
            "last_heartbeat_at",
            "checkpoint_ref",
            "result_ref",
        ):
            op.create_index(
                f"ix_ml_intern_training_attempts_{column}",
                "ml_intern_training_attempts",
                [column],
                unique=False,
            )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "ml_intern_training_events" not in existing:
        op.create_table(
            "ml_intern_training_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("dedupe_key", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["job_id"], ["ml_intern_training_jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "job_id",
                "sequence",
                name="uq_ml_intern_event_job_sequence",
            ),
            sa.UniqueConstraint(
                "job_id",
                "dedupe_key",
                name="uq_ml_intern_event_job_dedupe",
            ),
        )
        for column in (
            "job_id",
            "tenant_id",
            "owner_subject",
            "sequence",
            "event_type",
            "created_at",
        ):
            op.create_index(
                f"ix_ml_intern_training_events_{column}",
                "ml_intern_training_events",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_ml_intern_event_scope_job",
            "ml_intern_training_events",
            ["tenant_id", "owner_subject", "job_id"],
            unique=False,
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table_name in (
        "ml_intern_training_events",
        "ml_intern_training_attempts",
        "ml_intern_training_execution_leases",
        "ml_intern_training_capacity_leases",
        "ml_intern_training_jobs",
        "ml_intern_datasets",
    ):
        if table_name in existing:
            op.drop_table(table_name)
