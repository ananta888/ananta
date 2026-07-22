"""Add durable SFU flag projection, admission saga and scheduler state.

Revision ID: 5df4e5f6a7b8
Revises: 4cf3d4e5f6a7
Create Date: 2026-07-22 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "5df4e5f6a7b8"
down_revision: str | Sequence[str] | None = "4cf3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_broadcast_flag_projections" not in existing:
        op.create_table(
            "sfu_broadcast_flag_projections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("target_runtime_id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("runtime_control_mode", sa.String(), nullable=False),
            sa.Column("flag_version", sa.Integer(), nullable=False),
            sa.Column("cohort_version", sa.Integer(), nullable=False),
            sa.Column("config_digest", sa.String(), nullable=False),
            sa.Column("config_json", sa.JSON(), nullable=False),
            sa.Column("nonce", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("retry_max", sa.Integer(), nullable=False),
            sa.Column("ttl_seconds", sa.Float(), nullable=False),
            sa.Column("deadline_at", sa.Float(), nullable=False),
            sa.Column("next_attempt_at", sa.Float(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("acknowledged_at", sa.Float(), nullable=True),
            sa.Column("acknowledgement_digest", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("attempt >= 0", name="ck_sfu_flag_projection_attempt"),
            sa.CheckConstraint("retry_max >= 0", name="ck_sfu_flag_projection_retry"),
            sa.CheckConstraint("fencing_token > 0", name="ck_sfu_flag_projection_fencing"),
            sa.CheckConstraint("version > 0", name="ck_sfu_flag_projection_version"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "target_runtime_id", "flag_version", "cohort_version", "config_digest",
                name="uq_sfu_flag_projection_effective_version",
            ),
        )
        op.create_index(
            "ix_sfu_flag_projection_due",
            "sfu_broadcast_flag_projections",
            ["status", "priority", "next_attempt_at", "deadline_at"],
        )
        for name in ("tenant_id", "target_runtime_id", "cluster_id", "region", "flag_version", "cohort_version", "config_digest", "fencing_token", "status", "version", "updated_at"):
            op.create_index(f"ix_sfu_broadcast_flag_projections_{name}", "sfu_broadcast_flag_projections", [name])

    if "sfu_broadcast_runtime_projection_states" not in existing:
        op.create_table(
            "sfu_broadcast_runtime_projection_states",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("target_runtime_id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("flag_version", sa.Integer(), nullable=False),
            sa.Column("cohort_version", sa.Integer(), nullable=False),
            sa.Column("config_digest", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("admission_allowed", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("ack_expires_at", sa.Float(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("fencing_token > 0", name="ck_sfu_runtime_projection_fencing"),
            sa.CheckConstraint("version > 0", name="ck_sfu_runtime_projection_version"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "target_runtime_id", name="uq_sfu_runtime_projection_target"),
        )
        op.create_index(
            "ix_sfu_runtime_projection_admission",
            "sfu_broadcast_runtime_projection_states",
            ["tenant_id", "cluster_id", "region", "admission_allowed", "ack_expires_at"],
        )
        for name in ("tenant_id", "target_runtime_id", "cluster_id", "region", "flag_version", "cohort_version", "config_digest", "fencing_token", "admission_allowed", "status", "reason_code", "ack_expires_at", "version", "updated_at"):
            op.create_index(f"ix_sfu_broadcast_runtime_projection_states_{name}", "sfu_broadcast_runtime_projection_states", [name])

    if "sfu_broadcast_admission_operations" not in existing:
        op.create_table(
            "sfu_broadcast_admission_operations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("actor_digest", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("expected_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("current_step", sa.String(), nullable=False),
            sa.Column("applied_steps", sa.JSON(), nullable=False),
            sa.Column("external_request_ids", sa.JSON(), nullable=False),
            sa.Column("bindings_json", sa.JSON(), nullable=False),
            sa.Column("compensation_json", sa.JSON(), nullable=False),
            sa.Column("result_digest", sa.String(), nullable=True),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("deadline_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("completed_at", sa.Float(), nullable=True),
            sa.CheckConstraint("version > 0", name="ck_sfu_admission_operation_version"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "idempotency_key_digest", name="uq_sfu_admission_operation_idempotency"),
        )
        op.create_index("ix_sfu_admission_operation_recovery", "sfu_broadcast_admission_operations", ["status", "deadline_at", "updated_at"])
        for name in ("tenant_id", "room_id", "actor_digest", "operation", "idempotency_key_digest", "status", "current_step", "reason_code", "deadline_at", "version", "updated_at", "completed_at"):
            op.create_index(f"ix_sfu_broadcast_admission_operations_{name}", "sfu_broadcast_admission_operations", [name])

    if "sfu_broadcast_background_jobs" not in existing:
        op.create_table(
            "sfu_broadcast_background_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("job_name", sa.String(), nullable=False),
            sa.Column("partition_key", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=True),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("lease_expires_at", sa.Float(), nullable=True),
            sa.Column("interval_ms_min", sa.Integer(), nullable=False),
            sa.Column("batch_size_max", sa.Integer(), nullable=False),
            sa.Column("runtime_deadline_ms", sa.Integer(), nullable=False),
            sa.Column("retry_max", sa.Integer(), nullable=False),
            sa.Column("backoff_ms", sa.Integer(), nullable=False),
            sa.Column("jitter_ms", sa.Integer(), nullable=False),
            sa.Column("retention_seconds", sa.Integer(), nullable=False),
            sa.Column("resume_cursor", sa.String(), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("last_status", sa.String(), nullable=False),
            sa.Column("last_reason_code", sa.String(), nullable=True),
            sa.Column("last_started_at", sa.Float(), nullable=True),
            sa.Column("last_finished_at", sa.Float(), nullable=True),
            sa.Column("next_run_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("fencing_token >= 0", name="ck_sfu_background_job_fencing"),
            sa.CheckConstraint("version > 0", name="ck_sfu_background_job_version"),
            sa.CheckConstraint("interval_ms_min > 0", name="ck_sfu_background_job_interval"),
            sa.CheckConstraint("batch_size_max > 0", name="ck_sfu_background_job_batch"),
            sa.CheckConstraint("runtime_deadline_ms > 0", name="ck_sfu_background_job_deadline"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("job_name", "partition_key", name="uq_sfu_background_job_partition"),
        )
        op.create_index("ix_sfu_background_job_due", "sfu_broadcast_background_jobs", ["enabled", "next_run_at", "lease_expires_at"])
        for name in ("job_name", "partition_key", "enabled", "owner_id", "fencing_token", "lease_expires_at", "last_status", "last_reason_code", "last_started_at", "last_finished_at", "next_run_at", "version", "updated_at"):
            op.create_index(f"ix_sfu_broadcast_background_jobs_{name}", "sfu_broadcast_background_jobs", [name])


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_broadcast_admission_operations" in existing:
        open_operation = op.get_bind().execute(
            sa.text("SELECT 1 FROM sfu_broadcast_admission_operations WHERE status = 'open' LIMIT 1")
        ).first()
        if open_operation is not None:
            raise RuntimeError("refusing to drop open SFU admission operations")
    for table in (
        "sfu_broadcast_background_jobs",
        "sfu_broadcast_admission_operations",
        "sfu_broadcast_runtime_projection_states",
        "sfu_broadcast_flag_projections",
    ):
        if table in existing:
            op.drop_table(table)
