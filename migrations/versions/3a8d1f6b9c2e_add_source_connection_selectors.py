"""Add secret-free source connection selector bindings.

Revision ID: 3a8d1f6b9c2e
Revises: 2f7c9e1a4b6d
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "3a8d1f6b9c2e"
down_revision: str | Sequence[str] | None = "2f7c9e1a4b6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_connection_selectors",
        sa.Column(
            "connection_id",
            sa.String(69),
            sa.ForeignKey("source_connections.connection_id"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("public_connector_type", sa.String(32), nullable=False),
        sa.Column(
            "implementation_connector_type", sa.String(32), nullable=False
        ),
        sa.Column("selector_kind", sa.String(16), nullable=False),
        sa.Column("selector_id", sa.String(192), nullable=False),
        sa.Column("relative_path", sa.String(512), nullable=True),
        sa.Column("repository_identifier", sa.String(201), nullable=True),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "public_connector_type",
            "selector_id",
            "relative_path",
            name="uq_source_connection_selectors_coordinates",
        ),
        sa.CheckConstraint(
            "(public_connector_type = 'registered_workspace' AND "
            "implementation_connector_type = 'registered_workspace') OR "
            "(public_connector_type = 'local_directory' AND "
            "implementation_connector_type = 'local_directory') OR "
            "(public_connector_type = 'git' AND "
            "implementation_connector_type = 'generic_git') OR "
            "(public_connector_type = 'github' AND "
            "implementation_connector_type = 'github_repository')",
            name="ck_source_connection_selectors_connector_mapping",
        ),
        sa.CheckConstraint(
            "(selector_kind = 'workspace' AND relative_path IS NOT NULL "
            "AND repository_identifier IS NULL) OR "
            "(selector_kind = 'remote' AND relative_path IS NULL)",
            name="ck_source_connection_selectors_kind",
        ),
    )
    for column in (
        "tenant_id",
        "project_id",
        "owner_id",
        "public_connector_type",
        "implementation_connector_type",
        "selector_kind",
        "selector_id",
        "binding_digest",
    ):
        op.create_index(
            f"ix_source_connection_selectors_{column}",
            "source_connection_selectors",
            [column],
        )


def downgrade() -> None:
    op.drop_table("source_connection_selectors")
