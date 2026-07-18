"""Add atomic visual-process definition revision columns.

Revision ID: a5b6c7d8e9f0
Revises: z4a5b6c7d8e9
Create Date: 2026-07-18 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "z4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("definition_revision", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("base_graph_hash", sa.String(), nullable=False, server_default=""),
    sa.Column("graph_schema_version", sa.String(), nullable=False, server_default="1"),
    sa.Column("node_registry_version", sa.String(), nullable=False, server_default="1"),
)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "visual_process_graphs" not in set(inspector.get_table_names()):
        return
    existing = {item["name"] for item in inspector.get_columns("visual_process_graphs")}
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("visual_process_graphs", column)
    op.create_index(
        "ix_visual_process_graphs_definition_revision",
        "visual_process_graphs",
        ["definition_revision"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_visual_process_graphs_base_graph_hash",
        "visual_process_graphs",
        ["base_graph_hash"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "visual_process_graphs" not in set(inspector.get_table_names()):
        return
    indexes = {item["name"] for item in inspector.get_indexes("visual_process_graphs")}
    for index in (
        "ix_visual_process_graphs_base_graph_hash",
        "ix_visual_process_graphs_definition_revision",
    ):
        if index in indexes:
            op.drop_index(index, table_name="visual_process_graphs")
    existing = {item["name"] for item in inspector.get_columns("visual_process_graphs")}
    for name in (
        "node_registry_version",
        "graph_schema_version",
        "base_graph_hash",
        "definition_revision",
    ):
        if name in existing:
            op.drop_column("visual_process_graphs", name)
