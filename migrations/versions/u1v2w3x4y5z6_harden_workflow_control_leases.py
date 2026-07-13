"""Persist immutable plans and recoverable workflow-control leases.

Revision ID: u1v2w3x4y5z6
Revises: t1u2v3w4x5y6
Create Date: 2026-07-13 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "u1v2w3x4y5z6"
down_revision: str | Sequence[str] | None = "t1u2v3w4x5y6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    columns = {
        value["name"]
        for value in inspect(bind).get_columns("workflow_control_bindings")
    }
    additions = (
        ("execution_plan", sa.JSON(), "'{}'"),
        ("command_claim_expires_at", sa.Float(), "0"),
        ("scheduler_owner", sa.String(), "''"),
        ("scheduler_lease_expires_at", sa.Float(), "0"),
    )
    for name, column_type, default in additions:
        if name not in columns:
            op.add_column(
                "workflow_control_bindings",
                sa.Column(
                    name,
                    column_type,
                    nullable=False,
                    server_default=sa.text(default),
                ),
            )
    indexes = {
        value["name"]
        for value in inspect(bind).get_indexes("workflow_control_bindings")
    }
    for name, columns_value in (
        ("ix_workflow_control_bindings_command_claim_expires_at", ["command_claim_expires_at"]),
        ("ix_workflow_control_bindings_scheduler_owner", ["scheduler_owner"]),
        ("ix_workflow_control_bindings_scheduler_lease_expires_at", ["scheduler_lease_expires_at"]),
    ):
        if name not in indexes:
            op.create_index(name, "workflow_control_bindings", columns_value)
    if "workflow_runtime_capacity_lock" not in tables:
        op.create_table(
            "workflow_runtime_capacity_lock",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.Float(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.bulk_insert(
            sa.table(
                "workflow_runtime_capacity_lock",
                sa.column("id", sa.String()),
                sa.column("revision", sa.Integer()),
                sa.column("updated_at", sa.Float()),
            ),
            [{"id": "global", "revision": 0, "updated_at": 0.0}],
        )
    if "workflow_runtime_capacity_reservations" not in tables:
        op.create_table(
            "workflow_runtime_capacity_reservations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=False),
            sa.Column("hub_task_id", sa.String(), nullable=False, server_default=""),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("released_at", sa.Float(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, columns_value in (
            ("ix_workflow_runtime_capacity_reservations_tenant_id", ["tenant_id"]),
            ("ix_workflow_runtime_capacity_reservations_workflow_id", ["workflow_id"]),
            ("ix_workflow_runtime_capacity_reservations_run_id", ["run_id"]),
            ("ix_workflow_runtime_capacity_reservations_step_id", ["step_id"]),
            ("ix_workflow_runtime_capacity_reservations_hub_task_id", ["hub_task_id"]),
            ("ix_workflow_runtime_capacity_reservations_active", ["active"]),
            ("ix_workflow_runtime_capacity_reservations_created_at", ["created_at"]),
            ("ix_workflow_runtime_capacity_reservations_released_at", ["released_at"]),
            (
                "ix_workflow_runtime_capacity_active_tenant",
                ["active", "tenant_id"],
            ),
        ):
            op.create_index(
                name,
                "workflow_runtime_capacity_reservations",
                columns_value,
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workflow_runtime_capacity_reservations" in tables:
        op.drop_table("workflow_runtime_capacity_reservations")
    if "workflow_runtime_capacity_lock" in tables:
        op.drop_table("workflow_runtime_capacity_lock")
    indexes = {
        value["name"]
        for value in inspect(bind).get_indexes("workflow_control_bindings")
    }
    for name in (
        "ix_workflow_control_bindings_scheduler_lease_expires_at",
        "ix_workflow_control_bindings_scheduler_owner",
        "ix_workflow_control_bindings_command_claim_expires_at",
    ):
        if name in indexes:
            op.drop_index(name, table_name="workflow_control_bindings")
    columns = {
        value["name"]
        for value in inspect(bind).get_columns("workflow_control_bindings")
    }
    for name in (
        "scheduler_lease_expires_at",
        "scheduler_owner",
        "command_claim_expires_at",
        "execution_plan",
    ):
        if name in columns:
            op.drop_column("workflow_control_bindings", name)
