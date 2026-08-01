"""Add immutable remote source payload and revision bindings.

Revision ID: 2f7b9d1e3a5c
Revises: 7e2b0d3f5a8c
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2f7b9d1e3a5c"
down_revision = "7e2b0d3f5a8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "remote_source_payloads" not in existing:
        op.create_table(
            "remote_source_payloads",
            sa.Column("payload_digest", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("connector_type", sa.String(64), nullable=False),
            sa.Column("source_id", sa.String(255), nullable=False),
            sa.Column("connection_ref", sa.String(192), nullable=False),
            sa.Column("repository_key", sa.String(201), nullable=False),
            sa.Column("requested_ref", sa.String(255), nullable=False),
            sa.Column("commit_sha", sa.String(64), nullable=False),
            sa.Column("source_revision_digest", sa.String(64), nullable=False),
            sa.Column("git_manifest_digest", sa.String(64), nullable=False),
            sa.Column("authorization_binding_digest", sa.String(64), nullable=False),
            sa.Column("artifact_id", sa.String(128), nullable=False),
            sa.Column("artifact_filename", sa.String(128), nullable=False),
            sa.Column("artifact_version", sa.Integer(), nullable=False),
            sa.Column("byte_size", sa.BigInteger(), nullable=False),
            sa.Column("file_count", sa.Integer(), nullable=False),
            sa.Column("metrics_json", sa.Text(), nullable=False),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "project_id", "owner_id", "connector_type",
                "source_id", "connection_ref", "repository_key",
                "requested_ref", "commit_sha", "source_revision_digest",
                name="uq_remote_source_payload_coordinates",
            ),
        )
        op.create_index(
            "ix_remote_source_payload_lookup",
            "remote_source_payloads",
            ["tenant_id", "project_id", "owner_id", "connector_type", "connection_ref"],
        )
    existing = set(sa.inspect(bind).get_table_names())
    if "remote_source_payload_bindings" not in existing:
        op.create_table(
            "remote_source_payload_bindings",
            sa.Column("source_revision_id", sa.String(69), primary_key=True),
            sa.Column("connection_id", sa.String(69), nullable=False),
            sa.Column("payload_digest", sa.String(64), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("source_revision_digest", sa.String(64), nullable=False),
            sa.Column("manifest_digest", sa.String(64), nullable=False),
            sa.Column("bound_at_epoch", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["source_revision_id"], ["source_revisions.source_revision_id"]),
            sa.ForeignKeyConstraint(["connection_id"], ["source_connections.connection_id"]),
            sa.ForeignKeyConstraint(["payload_digest"], ["remote_source_payloads.payload_digest"]),
        )
        op.create_index(
            "ix_remote_source_payload_binding_connection",
            "remote_source_payload_bindings",
            ["tenant_id", "project_id", "connection_id"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "remote_source_payload_bindings" in existing:
        op.drop_table("remote_source_payload_bindings")
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "remote_source_payloads" in existing:
        op.drop_table("remote_source_payloads")
