"""Add Hub-owned provider budget reservations.

Revision ID: s1t2u3v4w5x6
Revises: r1s2t3u4v5w6
Create Date: 2026-07-13 18:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "s1t2u3v4w5x6"
down_revision: str | Sequence[str] | None = "r1s2t3u4v5w6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workflow_provider_budgets" not in tables:
        op.create_table(
            "workflow_provider_budgets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_micros", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("maximum_attempts", sa.Integer(), nullable=False),
            sa.Column("maximum_tokens", sa.Integer(), nullable=False),
            sa.Column("maximum_cost_micros", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "policy_version",
                name="uq_workflow_provider_budget_binding",
            ),
        )
        for name, columns in {
            "ix_workflow_provider_budgets_tenant_id": ("tenant_id",),
            "ix_workflow_provider_budgets_run_id": ("run_id",),
            "ix_workflow_provider_budgets_policy_version": ("policy_version",),
            "ix_workflow_provider_budgets_updated_at": ("updated_at",),
            "ix_workflow_provider_budgets_tenant_run": ("tenant_id", "run_id"),
        }.items():
            op.create_index(name, "workflow_provider_budgets", list(columns), unique=False)

    tables = set(inspect(bind).get_table_names())
    if "workflow_provider_budget_reservations" not in tables:
        op.create_table(
            "workflow_provider_budget_reservations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("budget_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("reservation_id", sa.String(), nullable=False),
            sa.Column("reserved_tokens", sa.Integer(), nullable=False),
            sa.Column("reserved_cost_micros", sa.Integer(), nullable=False),
            sa.Column("actual_total_tokens", sa.Integer(), nullable=True),
            sa.Column("reconciled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "reservation_id",
                name="uq_workflow_provider_budget_reservation",
            ),
        )
        for name, columns in {
            "ix_workflow_provider_budget_reservations_budget_id": ("budget_id",),
            "ix_workflow_provider_budget_reservations_tenant_id": ("tenant_id",),
            "ix_workflow_provider_budget_reservations_run_id": ("run_id",),
            "ix_workflow_provider_budget_reservations_policy_version": ("policy_version",),
            "ix_workflow_provider_budget_reservations_reconciled": ("reconciled",),
            "ix_workflow_provider_budget_reservations_created_at": ("created_at",),
            "ix_workflow_provider_budget_reservations_updated_at": ("updated_at",),
            "ix_workflow_provider_budget_reservations_budget": ("budget_id", "created_at"),
        }.items():
            op.create_index(
                name,
                "workflow_provider_budget_reservations",
                list(columns),
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workflow_provider_budget_reservations" in tables:
        op.drop_table("workflow_provider_budget_reservations")
    tables = set(inspect(bind).get_table_names())
    if "workflow_provider_budgets" in tables:
        op.drop_table("workflow_provider_budgets")
