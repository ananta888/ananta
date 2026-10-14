"""Add task-bound multi-tool dispatch receipts.

Revision ID: e0a2c4d6f8b1
Revises: d9f1b3c5e7a0
"""

import sqlalchemy as sa
from alembic import op

revision = "e0a2c4d6f8b1"
down_revision = "d9f1b3c5e7a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tool_plan_receipts",
        sa.Column("scope_digest", sa.String(), primary_key=True),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("response_json", sa.String(), nullable=False),
    )
    op.create_table(
        "tool_plan_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("event_json", sa.String(), nullable=False),
    )
    for field in ("tenant_id", "project_id", "task_id"):
        op.create_index("ix_tool_plan_events_" + field, "tool_plan_events", [field])


def downgrade():
    op.drop_table("tool_plan_events")
    op.drop_table("tool_plan_receipts")
