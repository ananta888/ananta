"""Add canonical source-control persistence and active-index bindings.

Revision ID: c9e1f3a5b7d9
Revises: b8d0f2a4c6e8
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e1f3a5b7d9"
down_revision: str | Sequence[str] | None = "b8d0f2a4c6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "source_connections" not in tables:
        op.create_table(
            "source_connections",
            sa.Column("connection_id", sa.String(69), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("connector_type", sa.String(64), nullable=False),
            sa.Column(
                "connection_identity_digest",
                sa.String(64),
                nullable=False,
            ),
            sa.Column("display_name", sa.String(200), nullable=False),
            sa.Column("sensitivity", sa.String(32), nullable=False),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.Column("updated_at_epoch", sa.Float(), nullable=False),
            sa.Column("disabled_at_epoch", sa.Float(), nullable=True),
            sa.Column("tombstoned_at_epoch", sa.Float(), nullable=True),
            sa.CheckConstraint(
                "lock_version >= 1",
                name="ck_source_connections_lock_version",
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "project_id",
                "connection_id",
                name="uq_source_connections_scope_id",
            ),
        )
        op.create_index(
            "ix_source_connections_scope",
            "source_connections",
            ["tenant_id", "project_id", "owner_id", "state"],
        )
    tables = _table_names()
    if "source_revisions" not in tables:
        op.create_table(
            "source_revisions",
            sa.Column("source_revision_id", sa.String(69), primary_key=True),
            sa.Column(
                "connection_id",
                sa.String(69),
                sa.ForeignKey("source_connections.connection_id"),
                nullable=False,
            ),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("connector_type", sa.String(64), nullable=False),
            sa.Column("sensitivity", sa.String(32), nullable=False),
            sa.Column("revision_token", sa.String(256), nullable=False),
            sa.Column("revision_digest", sa.String(64), nullable=False),
            sa.Column("content_manifest_id", sa.String(73), nullable=False),
            sa.Column(
                "content_manifest_digest",
                sa.String(64),
                nullable=False,
            ),
            sa.Column("admission_state", sa.String(32), nullable=False),
            sa.Column("captured_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "connection_id",
                "revision_digest",
                name="uq_source_revisions_connection_digest",
            ),
        )
        op.create_index(
            "ix_source_revisions_scope",
            "source_revisions",
            ["tenant_id", "project_id", "owner_id", "connection_id"],
        )
    tables = _table_names()
    if "source_access_grants" not in tables:
        op.create_table(
            "source_access_grants",
            sa.Column("grant_id", sa.String(70), primary_key=True),
            sa.Column("grant_family_id", sa.String(80), nullable=False),
            sa.Column("grant_version", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column(
                "source_revision_id",
                sa.String(69),
                sa.ForeignKey("source_revisions.source_revision_id"),
                nullable=False,
            ),
            sa.Column("destination_id", sa.String(68), nullable=False),
            sa.Column("operation", sa.String(32), nullable=False),
            sa.Column("transformation", sa.String(32), nullable=False),
            sa.Column("purpose", sa.String(128), nullable=False),
            sa.Column("policy_version", sa.String(128), nullable=False),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column("issued_at_epoch", sa.Float(), nullable=False),
            sa.Column("expires_at_epoch", sa.Float(), nullable=False),
            sa.Column(
                "rollback_of_grant_id",
                sa.String(70),
                nullable=True,
            ),
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("updated_at_epoch", sa.Float(), nullable=False),
            sa.CheckConstraint(
                "grant_version >= 1",
                name="ck_source_access_grants_version",
            ),
            sa.CheckConstraint(
                "lock_version >= 1",
                name="ck_source_access_grants_lock_version",
            ),
            sa.UniqueConstraint(
                "grant_family_id",
                "grant_version",
                name="uq_source_access_grants_family_version",
            ),
        )
        op.create_index(
            "ix_source_access_grants_scope_state",
            "source_access_grants",
            ["tenant_id", "project_id", "owner_id", "state"],
        )
        op.create_index(
            "ix_source_access_grants_binding",
            "source_access_grants",
            ["source_revision_id", "destination_id", "operation"],
        )
    tables = _table_names()
    if "source_access_grant_audit" not in tables:
        op.create_table(
            "source_access_grant_audit",
            sa.Column("audit_id", sa.String(70), primary_key=True),
            sa.Column("grant_id", sa.String(70), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("from_state", sa.String(32), nullable=True),
            sa.Column("to_state", sa.String(32), nullable=True),
            sa.Column("reason_code", sa.String(128), nullable=False),
            sa.Column("grant_lock_version", sa.Integer(), nullable=False),
            sa.Column("occurred_at_epoch", sa.Float(), nullable=False),
        )
        op.create_index(
            "ix_source_access_grant_audit_grant",
            "source_access_grant_audit",
            ["grant_id", "occurred_at_epoch"],
        )
    tables = _table_names()
    if "knowledge_index_source_bindings" not in tables:
        op.create_table(
            "knowledge_index_source_bindings",
            sa.Column("knowledge_index_id", sa.String(128), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column(
                "connection_id",
                sa.String(69),
                sa.ForeignKey("source_connections.connection_id"),
                nullable=False,
            ),
            sa.Column(
                "source_revision_id",
                sa.String(69),
                sa.ForeignKey("source_revisions.source_revision_id"),
                nullable=False,
            ),
            sa.Column("policy_snapshot_id", sa.String(128), nullable=False),
            sa.Column(
                "policy_snapshot_digest",
                sa.String(64),
                nullable=False,
            ),
            sa.Column(
                "index_contract_version",
                sa.String(128),
                nullable=False,
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column(
                "artifact_manifest_digest",
                sa.String(64),
                nullable=True,
            ),
            sa.Column(
                "activation_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        )
        op.create_index(
            "ix_knowledge_index_source_bindings_scope",
            "knowledge_index_source_bindings",
            ["tenant_id", "project_id", "owner_id", "connection_id"],
        )
        op.create_index(
            "ix_knowledge_index_source_bindings_reconcile",
            "knowledge_index_source_bindings",
            ["activation_requested", "status", "updated_at_epoch"],
        )
    tables = _table_names()
    if "knowledge_index_run_source_bindings" not in tables:
        op.create_table(
            "knowledge_index_run_source_bindings",
            sa.Column("index_run_id", sa.String(128), primary_key=True),
            sa.Column(
                "knowledge_index_id",
                sa.String(128),
                sa.ForeignKey(
                    "knowledge_index_source_bindings.knowledge_index_id"
                ),
                nullable=False,
            ),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column(
                "source_revision_id",
                sa.String(69),
                sa.ForeignKey("source_revisions.source_revision_id"),
                nullable=False,
            ),
            sa.Column("policy_snapshot_id", sa.String(128), nullable=False),
            sa.Column(
                "policy_snapshot_digest",
                sa.String(64),
                nullable=False,
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column(
                "artifact_manifest_digest",
                sa.String(64),
                nullable=True,
            ),
            sa.Column(
                "artifacts_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.Column("completed_at_epoch", sa.Float(), nullable=True),
        )
        op.create_index(
            "ix_knowledge_index_run_bindings_index",
            "knowledge_index_run_source_bindings",
            ["knowledge_index_id", "status", "artifacts_verified"],
        )
    tables = _table_names()
    if "active_knowledge_indexes" not in tables:
        op.create_table(
            "active_knowledge_indexes",
            sa.Column("active_index_id", sa.String(72), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column(
                "connection_id",
                sa.String(69),
                sa.ForeignKey("source_connections.connection_id"),
                nullable=False,
            ),
            sa.Column(
                "source_revision_id",
                sa.String(69),
                sa.ForeignKey("source_revisions.source_revision_id"),
                nullable=False,
            ),
            sa.Column(
                "policy_snapshot_digest",
                sa.String(64),
                nullable=False,
            ),
            sa.Column(
                "knowledge_index_id",
                sa.String(128),
                sa.ForeignKey(
                    "knowledge_index_source_bindings.knowledge_index_id"
                ),
                nullable=False,
            ),
            sa.Column(
                "previous_knowledge_index_id",
                sa.String(128),
                nullable=True,
            ),
            sa.Column(
                "generation",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("updated_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "project_id",
                "connection_id",
                name="uq_active_knowledge_indexes_scope",
            ),
        )
        op.create_index(
            "ix_active_knowledge_indexes_scope",
            "active_knowledge_indexes",
            ["tenant_id", "project_id", "owner_id", "connection_id"],
        )
    tables = _table_names()
    if "active_knowledge_index_events" not in tables:
        op.create_table(
            "active_knowledge_index_events",
            sa.Column("event_id", sa.String(70), primary_key=True),
            sa.Column("active_index_id", sa.String(72), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("connection_id", sa.String(69), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column(
                "from_knowledge_index_id",
                sa.String(128),
                nullable=True,
            ),
            sa.Column(
                "to_knowledge_index_id",
                sa.String(128),
                nullable=False,
            ),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("occurred_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "active_index_id",
                "generation",
                name="uq_active_knowledge_index_events_generation",
            ),
        )
        op.create_index(
            "ix_active_knowledge_index_events_scope",
            "active_knowledge_index_events",
            ["tenant_id", "project_id", "connection_id", "generation"],
        )


def downgrade() -> None:
    for table_name in (
        "active_knowledge_index_events",
        "active_knowledge_indexes",
        "knowledge_index_run_source_bindings",
        "knowledge_index_source_bindings",
        "source_access_grant_audit",
        "source_access_grants",
        "source_revisions",
        "source_connections",
    ):
        if table_name in _table_names():
            op.drop_table(table_name)
