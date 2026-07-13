"""Add restart-safe Temporal history projections.

Revision ID: o1p2q3r4s5t6
Revises: n1o2p3q4r5s6
Create Date: 2026-07-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "o1p2q3r4s5t6"
down_revision: str | Sequence[str] | None = "n1o2p3q4r5s6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "temporal_history_projections" not in tables:
        op.create_table(
            "temporal_history_projections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("namespace", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("temporal_run_id", sa.String(), nullable=False, server_default=""),
            sa.Column("correlation_id", sa.String(), nullable=False),
            sa.Column("last_event_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_page_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("mapping_version", sa.String(), nullable=False),
            sa.Column("consistency_state", sa.String(), nullable=False, server_default="stale"),
            sa.Column("reason_code", sa.String(), nullable=False, server_default=""),
            sa.Column("lag_events", sa.Integer(), nullable=True),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("raw_history_ref", sa.String(), nullable=False, server_default=""),
            sa.Column("activity_step_map", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, columns in (
            ("ix_temporal_history_projections_namespace", ["namespace"]),
            ("ix_temporal_history_projections_workflow_id", ["workflow_id"]),
            ("ix_temporal_history_projections_tenant_id", ["tenant_id"]),
            ("ix_temporal_history_projections_run_id", ["run_id"]),
            ("ix_temporal_history_projections_temporal_run_id", ["temporal_run_id"]),
            ("ix_temporal_history_projections_consistency_state", ["consistency_state"]),
            ("ix_temporal_history_projections_updated_at", ["updated_at"]),
        ):
            op.create_index(name, "temporal_history_projections", columns, unique=False)

    tables = set(inspect(bind).get_table_names())
    if "temporal_projected_events" not in tables:
        op.create_table(
            "temporal_projected_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("projection_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("temporal_run_id", sa.String(), nullable=False),
            sa.Column("temporal_event_id", sa.Integer(), nullable=False),
            sa.Column("temporal_event_type", sa.String(), nullable=False),
            sa.Column("canonical_event", sa.JSON(), nullable=False),
            sa.Column("occurred_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(
                ["projection_id"],
                ["temporal_history_projections.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "projection_id",
                "temporal_event_id",
                name="uq_temporal_projected_event_sequence",
            ),
        )
        for name, columns in (
            ("ix_temporal_projected_events_projection_id", ["projection_id"]),
            ("ix_temporal_projected_events_tenant_id", ["tenant_id"]),
            ("ix_temporal_projected_events_workflow_id", ["workflow_id"]),
            ("ix_temporal_projected_events_run_id", ["run_id"]),
            ("ix_temporal_projected_events_temporal_run_id", ["temporal_run_id"]),
            ("ix_temporal_projected_events_temporal_event_id", ["temporal_event_id"]),
        ):
            op.create_index(name, "temporal_projected_events", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "temporal_projected_events" in tables:
        op.drop_table("temporal_projected_events")
    if "temporal_history_projections" in tables:
        op.drop_table("temporal_history_projections")
