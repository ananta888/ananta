"""Add persistent Organization runtime ledgers.

Revision ID: 5a0e2b4c6d8f
Revises: 4f9d1a3b5c7e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.enterprise_organization_schema_v1 import enterprise_organization_tables_v1

revision = "5a0e2b4c6d8f"
down_revision = "4f9d1a3b5c7e"
branch_labels = None
depends_on = None


ORGANIZATION_RUNTIME_TABLE_ORDER = (
    "organization_budget_usage",
    "organization_budget_reservations",
    "organization_runtime_events",
    "organization_team_handoffs",
    "organization_workflow_loop_states",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for name, table in _runtime_tables().items():
        if name not in existing:
            table.create(bind=bind, checkfirst=True)
            existing.add(name)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _runtime_tables()
    existing = set(sa.inspect(bind).get_table_names())
    for name in reversed(ORGANIZATION_RUNTIME_TABLE_ORDER):
        if name in existing:
            tables[name].drop(bind=bind, checkfirst=True)


def _runtime_tables() -> dict[str, sa.Table]:
    tables = enterprise_organization_tables_v1()
    return {name: tables[name] for name in ORGANIZATION_RUNTIME_TABLE_ORDER}
