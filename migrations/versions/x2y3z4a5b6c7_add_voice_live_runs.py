"""Add durable Hub-owned Voice live-run orchestration ledgers.

Revision ID: x2y3z4a5b6c7
Revises: w1x2y3z4a5b6
Create Date: 2026-07-15 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "x2y3z4a5b6c7"
down_revision: str | Sequence[str] | None = "w1x2y3z4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "voice_live_runs" not in existing:
        op.create_table(
            "voice_live_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("profile_id", sa.String(), nullable=False, server_default="default"),
            sa.Column("configuration_session_id", sa.String(), nullable=True),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("parent_task_id", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("language", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("segment_duration_seconds", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("max_duration_seconds", sa.Integer(), nullable=False, server_default="28800"),
            sa.Column("overlap_milliseconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_local_sequence", sa.Integer(), nullable=True),
            sa.Column("expected_last_sequence", sa.Integer(), nullable=True),
            sa.Column("reported_gap_sequences", sa.JSON(), nullable=True, server_default="[]"),
            sa.Column("last_heartbeat_at", sa.Float(), nullable=False),
            sa.Column("capture_deadline_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("final_result_ref", sa.String(), nullable=True),
            sa.Column("stop_reason", sa.String(), nullable=True),
            sa.Column("maintenance_lease_token", sa.String(), nullable=True),
            sa.Column("maintenance_lease_expires_at", sa.Float(), nullable=True),
            sa.Column("maintenance_reconciled_at", sa.Float(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("stopped_at", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "owner_subject",
                "idempotency_key_digest",
                name="uq_voice_live_runs_scope_idempotency",
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "profile_id",
            "configuration_session_id",
            "idempotency_key_digest",
            "parent_task_id",
            "status",
            "last_heartbeat_at",
            "capture_deadline_at",
            "expires_at",
            "final_result_ref",
            "maintenance_lease_expires_at",
            "maintenance_reconciled_at",
        ):
            op.create_index(
                f"ix_voice_live_runs_{column}",
                "voice_live_runs",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_voice_live_runs_scope_profile",
            "voice_live_runs",
            ["tenant_id", "owner_subject", "profile_id"],
            unique=False,
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "voice_live_run_segments" not in existing:
        op.create_table(
            "voice_live_run_segments",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="processing"),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("audio_binding", sa.String(), nullable=True),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("result_ref", sa.String(), nullable=True),
            sa.Column("started_at_ms", sa.Integer(), nullable=False),
            sa.Column("ended_at_ms", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("overlap_milliseconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("failure_code", sa.String(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("completed_at", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["voice_live_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "sequence",
                name="uq_voice_live_run_segments_run_sequence",
            ),
        )
        for column in (
            "run_id",
            "tenant_id",
            "owner_subject",
            "sequence",
            "status",
            "task_id",
            "result_ref",
        ):
            op.create_index(
                f"ix_voice_live_run_segments_{column}",
                "voice_live_run_segments",
                [column],
                unique=False,
            )
        op.create_index(
            "ix_voice_live_run_segments_scope_run",
            "voice_live_run_segments",
            ["tenant_id", "owner_subject", "run_id"],
            unique=False,
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "voice_live_run_segments" in existing:
        op.drop_table("voice_live_run_segments")
    if "voice_live_runs" in existing:
        op.drop_table("voice_live_runs")
