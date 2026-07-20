"""Add durable Hub-owned speech adaptation control-plane tables.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-19 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_adaptation_jobs" not in existing:
        op.create_table(
            "speech_adaptation_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("idempotency_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("admission_request_payload", sa.JSON(), nullable=True),
            sa.Column("contract_payload", sa.JSON(), nullable=True),
            sa.Column("result_payload", sa.JSON(), nullable=True),
            sa.Column("worker_status", sa.String(), nullable=True),
            sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
            sa.Column("next_dispatch_at_ms", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("terminal_at_ms", sa.BigInteger(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "idempotency_digest",
                name="uq_speech_adaptation_job_scope_idempotency",
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "task_id",
            "idempotency_digest",
            "request_digest",
            "status",
            "worker_status",
            "next_dispatch_at_ms",
            "created_at_ms",
            "terminal_at_ms",
        ):
            op.create_index(
                f"ix_speech_adaptation_jobs_{column}",
                "speech_adaptation_jobs",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_speech_adaptation_job_dispatch",
            "speech_adaptation_jobs",
            ["status", "next_dispatch_at_ms", "updated_at_ms"],
            unique=False,
        )
        op.create_index(
            "ix_speech_adaptation_job_scope_created",
            "speech_adaptation_jobs",
            ["tenant_id", "owner_subject", "created_at_ms"],
            unique=False,
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_adaptation_capacity_leases" not in existing:
        op.create_table(
            "speech_adaptation_capacity_leases",
            sa.Column("slot", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("lease_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.BigInteger(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("slot"),
            sa.UniqueConstraint("job_id", name="uq_speech_adaptation_capacity_job"),
            sa.UniqueConstraint("lease_id", name="uq_speech_adaptation_capacity_lease"),
            sa.UniqueConstraint("epoch", name="uq_speech_adaptation_capacity_epoch"),
        )
        for column in ("job_id", "lease_id", "epoch", "expires_at_ms"):
            op.create_index(
                f"ix_speech_adaptation_capacity_leases_{column}",
                "speech_adaptation_capacity_leases",
                [column],
                unique=False,
            )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_adaptation_artifacts" not in existing:
        op.create_table(
            "speech_adaptation_artifacts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("artifact_ref", sa.String(), nullable=False),
            sa.Column("sha256", sa.String(), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("media_type", sa.String(), nullable=False),
            sa.Column("storage_ref", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(["job_id"], ["speech_adaptation_jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "job_id",
                "attempt_id",
                "artifact_ref",
                name="uq_speech_adaptation_artifact_attempt_ref",
            ),
            sa.UniqueConstraint(
                "job_id",
                "attempt_id",
                "media_type",
                name="uq_speech_adaptation_artifact_attempt_media",
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "job_id",
            "attempt_id",
            "artifact_ref",
            "sha256",
            "state",
            "created_at_ms",
        ):
            op.create_index(
                f"ix_speech_adaptation_artifacts_{column}",
                "speech_adaptation_artifacts",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_speech_adaptation_artifact_scope",
            "speech_adaptation_artifacts",
            ["tenant_id", "owner_subject", "job_id"],
            unique=False,
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "speech_adaptation_artifacts",
        "speech_adaptation_capacity_leases",
        "speech_adaptation_jobs",
    ):
        if table in existing:
            op.drop_table(table)
