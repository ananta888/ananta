"""Add durable terminal trace reconciliation state to workflow bindings.

Terminal trace projection was best effort: a failed projection was audited and
then lost, so a run could end without its final trace ever reaching a read
model. These columns make the pending state durable, so a restart resumes the
reconciliation instead of dropping it.

Revision ID: a1c3e5f7b9d2
Revises: f0b2d4e6a8c1
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "a1c3e5f7b9d2"
down_revision: str | Sequence[str] | None = "f0b2d4e6a8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_control_bindings"
_PENDING_INDEX = "ix_workflow_control_bindings_trace_pending"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, str | bool], ...] = (
    ("trace_pending", sa.Boolean(), False),
    ("trace_pending_revision", sa.Integer(), "0"),
    ("trace_projected_revision", sa.Integer(), "0"),
    ("trace_cursor", sa.String(256), ""),
)


def _existing_columns(connection: sa.Connection) -> set[str]:
    inspector = inspect(connection)
    if _TABLE not in set(inspector.get_table_names()):
        raise RuntimeError("workflow_control_binding_trace_prerequisite_missing")
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def _existing_indexes(connection: sa.Connection) -> set[str]:
    return {str(index["name"]) for index in inspect(connection).get_indexes(_TABLE)}


def upgrade() -> None:
    connection = op.get_bind()
    present = _existing_columns(connection)
    for name, column_type, server_default in _COLUMNS:
        if name in present:
            continue
        op.add_column(
            _TABLE,
            sa.Column(
                name,
                column_type,
                nullable=False,
                server_default=(
                    sa.text("'%s'" % server_default)
                    if isinstance(column_type, sa.String)
                    else (
                        sa.false()
                        if isinstance(column_type, sa.Boolean)
                        else sa.text(server_default)
                    )
                ),
            ),
        )
    if _PENDING_INDEX not in _existing_indexes(connection):
        op.create_index(_PENDING_INDEX, _TABLE, ["trace_pending", "tenant_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if _PENDING_INDEX in _existing_indexes(connection):
        op.drop_index(_PENDING_INDEX, table_name=_TABLE)
    present = _existing_columns(connection)
    for name, _column_type, _default in reversed(_COLUMNS):
        if name in present:
            op.drop_column(_TABLE, name)
