"""Add durable workflow runtime stores and transactional outbox.

Revision ID: p1q2r3s4t5u6
Revises: o1p2q3r4s5t6
Create Date: 2026-07-13 13:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "p1q2r3s4t5u6"
down_revision: str | Sequence[str] | None = "o1p2q3r4s5t6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "workflow_runtime_events" not in tables:
        op.create_table(
            "workflow_runtime_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("dedupe_key", sa.String(), nullable=False),
            sa.Column("content_hash", sa.String(), nullable=False),
            sa.Column("occurred_at", sa.Float(), nullable=False),
            sa.Column("canonical_event", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "run_id", "sequence", name="uq_workflow_runtime_event_sequence"
            ),
            sa.UniqueConstraint(
                "tenant_id", "run_id", "dedupe_key", name="uq_workflow_runtime_event_dedupe"
            ),
            sa.UniqueConstraint(
                "tenant_id", "run_id", "event_id", name="uq_workflow_runtime_event_id"
            ),
        )
        _indexes(
            "workflow_runtime_events",
            {
                "ix_workflow_runtime_events_tenant_id": ("tenant_id",),
                "ix_workflow_runtime_events_workflow_id": ("workflow_id",),
                "ix_workflow_runtime_events_run_id": ("run_id",),
                "ix_workflow_runtime_events_event_type": ("event_type",),
                "ix_workflow_runtime_events_occurred_at": ("occurred_at",),
                "ix_workflow_runtime_events_tenant_run_sequence": (
                    "tenant_id",
                    "run_id",
                    "sequence",
                ),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_runtime_checkpoints" not in tables:
        op.create_table(
            "workflow_runtime_checkpoints",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("checkpoint_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("signed_checkpoint", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "checkpoint_id", name="uq_workflow_runtime_checkpoint_id"
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "task_id",
                "revision",
                name="uq_workflow_runtime_checkpoint_revision",
            ),
        )
        _indexes(
            "workflow_runtime_checkpoints",
            {
                "ix_workflow_runtime_checkpoints_tenant_id": ("tenant_id",),
                "ix_workflow_runtime_checkpoints_workflow_id": ("workflow_id",),
                "ix_workflow_runtime_checkpoints_run_id": ("run_id",),
                "ix_workflow_runtime_checkpoints_task_id": ("task_id",),
                "ix_workflow_runtime_checkpoints_created_at": ("created_at",),
                "ix_workflow_runtime_checkpoints_latest": (
                    "tenant_id",
                    "run_id",
                    "task_id",
                    "revision",
                ),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_side_effect_ledger" not in tables:
        op.create_table(
            "workflow_side_effect_ledger",
            sa.Column("operation_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("record", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("operation_id"),
        )
        _indexes(
            "workflow_side_effect_ledger",
            {
                "ix_workflow_side_effect_ledger_tenant_id": ("tenant_id",),
                "ix_workflow_side_effect_ledger_workflow_id": ("workflow_id",),
                "ix_workflow_side_effect_ledger_run_id": ("run_id",),
                "ix_workflow_side_effect_ledger_step_id": ("step_id",),
                "ix_workflow_side_effect_ledger_status": ("status",),
                "ix_workflow_side_effect_ledger_updated_at": ("updated_at",),
                "ix_workflow_side_effect_ledger_tenant_run": ("tenant_id", "run_id"),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_execution_ownership" not in tables:
        op.create_table(
            "workflow_execution_ownership",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("lease_expires_at", sa.Float(), nullable=False),
            sa.Column("last_heartbeat_at", sa.Float(), nullable=False),
            sa.Column("ownership", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "run_id", "step_id", name="uq_workflow_execution_ownership_step"
            ),
        )
        _indexes(
            "workflow_execution_ownership",
            {
                "ix_workflow_execution_ownership_tenant_id": ("tenant_id",),
                "ix_workflow_execution_ownership_workflow_id": ("workflow_id",),
                "ix_workflow_execution_ownership_run_id": ("run_id",),
                "ix_workflow_execution_ownership_step_id": ("step_id",),
                "ix_workflow_execution_ownership_attempt_id": ("attempt_id",),
                "ix_workflow_execution_ownership_owner_id": ("owner_id",),
                "ix_workflow_execution_ownership_status": ("status",),
                "ix_workflow_execution_ownership_lease_expires_at": ("lease_expires_at",),
                "ix_workflow_execution_ownership_lease": (
                    "tenant_id",
                    "status",
                    "lease_expires_at",
                ),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_execution_attempt_history" not in tables:
        op.create_table(
            "workflow_execution_attempt_history",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("recorded_at", sa.Float(), nullable=False),
            sa.Column("ownership", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "step_id",
                "revision",
                name="uq_workflow_execution_attempt_revision",
            ),
        )
        _indexes(
            "workflow_execution_attempt_history",
            {
                "ix_workflow_execution_attempt_history_tenant_id": ("tenant_id",),
                "ix_workflow_execution_attempt_history_workflow_id": ("workflow_id",),
                "ix_workflow_execution_attempt_history_run_id": ("run_id",),
                "ix_workflow_execution_attempt_history_step_id": ("step_id",),
                "ix_workflow_execution_attempt_history_attempt_id": ("attempt_id",),
                "ix_workflow_execution_attempt_history_owner_id": ("owner_id",),
                "ix_workflow_execution_attempt_history_status": ("status",),
                "ix_workflow_execution_attempt_history_recorded_at": ("recorded_at",),
                "ix_workflow_execution_attempt_history_run": (
                    "tenant_id",
                    "run_id",
                    "step_id",
                    "revision",
                ),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_retry_budgets" not in tables:
        op.create_table(
            "workflow_retry_budgets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("maximum", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "run_id", name="uq_workflow_retry_budget_run"),
        )
        _indexes(
            "workflow_retry_budgets",
            {
                "ix_workflow_retry_budgets_tenant_id": ("tenant_id",),
                "ix_workflow_retry_budgets_run_id": ("run_id",),
                "ix_workflow_retry_budgets_updated_at": ("updated_at",),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_retry_consumptions" not in tables:
        op.create_table(
            "workflow_retry_consumptions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("retry_id", sa.String(), nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("consumed_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "run_id", "retry_id", name="uq_workflow_retry_consumption_id"
            ),
        )
        _indexes(
            "workflow_retry_consumptions",
            {
                "ix_workflow_retry_consumptions_tenant_id": ("tenant_id",),
                "ix_workflow_retry_consumptions_run_id": ("run_id",),
                "ix_workflow_retry_consumptions_category": ("category",),
                "ix_workflow_retry_consumptions_consumed_at": ("consumed_at",),
                "ix_workflow_retry_consumptions_run": ("tenant_id", "run_id"),
            },
        )

    tables = set(inspect(bind).get_table_names())
    if "workflow_runtime_outbox" not in tables:
        op.create_table(
            "workflow_runtime_outbox",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("aggregate_id", sa.String(), nullable=False),
            sa.Column("topic", sa.String(), nullable=False),
            sa.Column("dedupe_key", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("available_at", sa.Float(), nullable=False),
            sa.Column("claimed_by", sa.String(), nullable=False, server_default=""),
            sa.Column("claim_expires_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("published_at", sa.Float(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "topic", "dedupe_key", name="uq_workflow_runtime_outbox_dedupe"
            ),
        )
        _indexes(
            "workflow_runtime_outbox",
            {
                "ix_workflow_runtime_outbox_tenant_id": ("tenant_id",),
                "ix_workflow_runtime_outbox_aggregate_id": ("aggregate_id",),
                "ix_workflow_runtime_outbox_topic": ("topic",),
                "ix_workflow_runtime_outbox_status": ("status",),
                "ix_workflow_runtime_outbox_available_at": ("available_at",),
                "ix_workflow_runtime_outbox_claim_expires_at": ("claim_expires_at",),
                "ix_workflow_runtime_outbox_created_at": ("created_at",),
                "ix_workflow_runtime_outbox_published_at": ("published_at",),
                "ix_workflow_runtime_outbox_delivery": (
                    "tenant_id",
                    "status",
                    "available_at",
                    "created_at",
                ),
            },
        )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "workflow_runtime_outbox",
        "workflow_retry_consumptions",
        "workflow_retry_budgets",
        "workflow_execution_attempt_history",
        "workflow_execution_ownership",
        "workflow_side_effect_ledger",
        "workflow_runtime_checkpoints",
        "workflow_runtime_events",
    ):
        if table in tables:
            op.drop_table(table)


def _indexes(table: str, definitions: dict[str, tuple[str, ...]]) -> None:
    for name, columns in definitions.items():
        op.create_index(name, table, list(columns), unique=False)
