"""Add revisioned provisional and corrected Voice live-run results.

Revision ID: y3z4a5b6c7d8
Revises: x2y3z4a5b6c7
Create Date: 2026-07-15 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "y3z4a5b6c7d8"
down_revision: str | Sequence[str] | None = "x2y3z4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "voice_live_runs" in inspector.get_table_names():
        run_columns = {item["name"] for item in inspector.get_columns("voice_live_runs")}
        if "timeline_revision" not in run_columns:
            op.add_column(
                "voice_live_runs",
                sa.Column("timeline_revision", sa.Integer(), nullable=False, server_default="0"),
            )
            op.create_index(
                "ix_voice_live_runs_timeline_revision",
                "voice_live_runs",
                ["timeline_revision"],
                unique=False,
            )

    inspector = inspect(op.get_bind())
    if "voice_live_run_segments" not in inspector.get_table_names():
        return
    segment_columns = {item["name"] for item in inspector.get_columns("voice_live_run_segments")}
    additions = (
        ("provisional_result_ref", sa.String(), True, None),
        ("correction_task_id", sa.String(), True, None),
        ("correction_status", sa.String(), False, "not_requested"),
        ("correction_configuration_digest", sa.String(), True, None),
        ("correction_spec_ref", sa.String(), True, None),
        ("correction_attempt_count", sa.Integer(), False, "0"),
        ("correction_failure_code", sa.String(), True, None),
        ("text_revision", sa.Integer(), False, "0"),
        ("timeline_revision", sa.Integer(), False, "0"),
        ("correction_started_at", sa.Float(), True, None),
        ("correction_completed_at", sa.Float(), True, None),
    )
    for name, column_type, nullable, default in additions:
        if name not in segment_columns:
            op.add_column(
                "voice_live_run_segments",
                sa.Column(name, column_type, nullable=nullable, server_default=default),
            )

    # Existing completed segments predate provisional presentation and are
    # already authoritative. Failed/processing rows intentionally stay at 0.
    op.execute(
        sa.text(
            "UPDATE voice_live_run_segments "
            "SET provisional_result_ref = result_ref, text_revision = 2, "
            "correction_status = 'not_requested' "
            "WHERE status = 'completed' AND result_ref IS NOT NULL AND text_revision = 0"
        )
    )
    for column in (
        "provisional_result_ref",
        "correction_task_id",
        "correction_status",
        "correction_spec_ref",
        "timeline_revision",
    ):
        index_name = f"ix_voice_live_run_segments_{column}"
        existing_indexes = {item.get("name") for item in inspect(op.get_bind()).get_indexes("voice_live_run_segments")}
        if index_name not in existing_indexes:
            op.create_index(index_name, "voice_live_run_segments", [column], unique=False)


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "voice_live_run_segments" in inspector.get_table_names():
        columns = {item["name"] for item in inspector.get_columns("voice_live_run_segments")}
        indexes = {item.get("name") for item in inspector.get_indexes("voice_live_run_segments")}
        for column in (
            "timeline_revision",
            "correction_status",
            "correction_spec_ref",
            "correction_task_id",
            "provisional_result_ref",
        ):
            index_name = f"ix_voice_live_run_segments_{column}"
            if index_name in indexes:
                op.drop_index(index_name, table_name="voice_live_run_segments")
        for column in (
            "correction_completed_at",
            "correction_started_at",
            "timeline_revision",
            "text_revision",
            "correction_failure_code",
            "correction_attempt_count",
            "correction_configuration_digest",
            "correction_spec_ref",
            "correction_status",
            "correction_task_id",
            "provisional_result_ref",
        ):
            if column in columns:
                op.drop_column("voice_live_run_segments", column)
    inspector = inspect(op.get_bind())
    if "voice_live_runs" in inspector.get_table_names():
        columns = {item["name"] for item in inspector.get_columns("voice_live_runs")}
        indexes = {item.get("name") for item in inspector.get_indexes("voice_live_runs")}
        if "ix_voice_live_runs_timeline_revision" in indexes:
            op.drop_index("ix_voice_live_runs_timeline_revision", table_name="voice_live_runs")
        if "timeline_revision" in columns:
            op.drop_column("voice_live_runs", "timeline_revision")
