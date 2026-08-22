"""add HRM experiment control plane

Revision ID: d4f6a8c0e2b5
Revises: c3e5a7b9d1f4
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d4f6a8c0e2b5"
down_revision: str | Sequence[str] | None = "c3e5a7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    tables = {
        "hrm_worker_capabilities": [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("worker_id", sa.String(), nullable=False),
            sa.Column("worker_url", sa.String(), nullable=False, unique=True),
            sa.Column("capability_digest", sa.String(), nullable=False),
            sa.Column("projection", sa.JSON(), nullable=False),
            sa.Column("observed_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
        ],
        "hrm_datasets": [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("puzzle_type", sa.String(), nullable=False),
            sa.Column("content_digest", sa.String(), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("records", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("tenant_id", "project_id", "dataset_id", name="uq_hrm_dataset_scope_id"),
            sa.UniqueConstraint("tenant_id", "project_id", "content_digest", name="uq_hrm_dataset_scope_digest"),
        ],
        "hrm_checkpoints": [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("checkpoint_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("content_digest", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("tenant_id", "project_id", "checkpoint_id", name="uq_hrm_checkpoint_scope_id"),
            sa.UniqueConstraint("tenant_id", "project_id", "content_digest", name="uq_hrm_checkpoint_scope_digest"),
        ],
        "hrm_runs": [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("profile_id", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("dataset_digest", sa.String(), nullable=False),
            sa.Column("checkpoint_id", sa.String(), nullable=True),
            sa.Column("checkpoint_digest", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("intent", sa.JSON(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("capability_digest", sa.String(), nullable=False),
            sa.Column("policy_digest", sa.String(), nullable=False),
            sa.Column("worker_url", sa.String(), nullable=True),
            sa.Column("worker_job_id", sa.String(), nullable=True),
            sa.Column("assignment_id", sa.String(), nullable=True),
            sa.Column("dispatch_lease_id", sa.String(), nullable=True),
            sa.Column("attempt_id", sa.String(), nullable=True),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("deadline_epoch_ms", sa.Integer(), nullable=True),
            sa.Column("execution_envelope", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("started_at", sa.Float(), nullable=True),
            sa.Column("finished_at", sa.Float(), nullable=True),
            sa.UniqueConstraint("tenant_id", "owner_subject", "idempotency_key_digest", name="uq_hrm_run_scope_idempotency"),
        ],
        "hrm_run_events": [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), sa.ForeignKey("hrm_runs.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("run_id", "sequence", name="uq_hrm_run_event_sequence"),
        ],
        "hrm_evaluation_reports": [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("evaluation_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), sa.ForeignKey("hrm_runs.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("content_digest", sa.String(), nullable=False),
            sa.Column("report", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("tenant_id", "owner_subject", "idempotency_key_digest", name="uq_hrm_report_scope_idempotency"),
        ],
    }
    for name, columns in tables.items():
        if name not in existing:
            op.create_table(name, *columns)
    indexes = (
        ("ix_hrm_worker_capability_expiry", "hrm_worker_capabilities", ["expires_at", "observed_at"]),
        ("ix_hrm_dataset_scope_created", "hrm_datasets", ["tenant_id", "project_id", "created_at", "id"]),
        ("ix_hrm_checkpoint_scope_created", "hrm_checkpoints", ["tenant_id", "project_id", "created_at", "id"]),
        ("ix_hrm_run_scope_created", "hrm_runs", ["tenant_id", "project_id", "created_at", "id"]),
        ("ix_hrm_run_event_scope", "hrm_run_events", ["tenant_id", "project_id", "run_id", "sequence"]),
        ("ix_hrm_report_scope_created", "hrm_evaluation_reports", ["tenant_id", "project_id", "created_at", "id"]),
    )
    for name, table, columns in indexes:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table_name in (
        "hrm_evaluation_reports",
        "hrm_run_events",
        "hrm_runs",
        "hrm_checkpoints",
        "hrm_datasets",
        "hrm_worker_capabilities",
    ):
        if table_name in existing:
            op.drop_table(table_name)
