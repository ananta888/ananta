"""Add persistent scoped Hub Git authorization registry.

Revision ID: 1e6a8c0d2f4b
Revises: 0d5f7b9c1e3a
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "1e6a8c0d2f4b"
down_revision = "0d5f7b9c1e3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "hub_git_remote_registrations" not in existing:
        op.create_table(
            "hub_git_remote_registrations",
            sa.Column("registration_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(191), nullable=False),
            sa.Column("project_id", sa.String(191), nullable=False),
            sa.Column("owner_id", sa.String(191), nullable=False),
            sa.Column("connection_ref", sa.String(192), nullable=False),
            sa.Column(
                "repository_key",
                sa.String(201),
                nullable=False,
                server_default="",
            ),
            sa.Column("authorization_kind", sa.String(32), nullable=False),
            sa.Column("remote_url", sa.Text(), nullable=False),
            sa.Column("credential_ref", sa.Text(), nullable=True),
            sa.Column("credential_username", sa.String(256), nullable=True),
            sa.Column("authorization_state", sa.String(32), nullable=False),
            sa.Column(
                "granted_scopes_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column(
                "current_revision",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("created_at_epoch", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_epoch", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "project_id",
                "owner_id",
                "connection_ref",
                "repository_key",
                name="uq_hub_git_remote_registration_scope",
            ),
        )
        op.create_index(
            "ix_hub_git_remote_registration_lookup",
            "hub_git_remote_registrations",
            [
                "tenant_id",
                "project_id",
                "owner_id",
                "connection_ref",
                "repository_key",
            ],
        )
        op.create_index(
            "ix_hub_git_remote_registrations_tenant_id",
            "hub_git_remote_registrations",
            ["tenant_id"],
        )
        op.create_index(
            "ix_hub_git_remote_registrations_project_id",
            "hub_git_remote_registrations",
            ["project_id"],
        )
        op.create_index(
            "ix_hub_git_remote_registrations_owner_id",
            "hub_git_remote_registrations",
            ["owner_id"],
        )

    existing = set(sa.inspect(bind).get_table_names())
    if "hub_git_remote_registration_revisions" not in existing:
        op.create_table(
            "hub_git_remote_registration_revisions",
            sa.Column("revision_id", sa.String(64), primary_key=True),
            sa.Column("registration_id", sa.String(64), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(191), nullable=False),
            sa.Column("project_id", sa.String(191), nullable=False),
            sa.Column("owner_id", sa.String(191), nullable=False),
            sa.Column("connection_ref", sa.String(192), nullable=False),
            sa.Column(
                "repository_key",
                sa.String(201),
                nullable=False,
                server_default="",
            ),
            sa.Column("authorization_kind", sa.String(32), nullable=False),
            sa.Column("remote_url", sa.Text(), nullable=False),
            sa.Column("credential_ref", sa.Text(), nullable=True),
            sa.Column("credential_username", sa.String(256), nullable=True),
            sa.Column("authorization_state", sa.String(32), nullable=False),
            sa.Column("granted_scopes_json", sa.Text(), nullable=False),
            sa.Column("snapshot_digest", sa.String(64), nullable=False),
            sa.Column("actor_id", sa.String(192), nullable=False),
            sa.Column("reason_code", sa.String(192), nullable=False),
            sa.Column("created_at_epoch", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(
                ["registration_id"],
                ["hub_git_remote_registrations.registration_id"],
                name="fk_hub_git_remote_revision_registration",
            ),
            sa.UniqueConstraint(
                "registration_id",
                "revision",
                name="uq_hub_git_remote_registration_revision",
            ),
        )
        op.create_index(
            "ix_hub_git_remote_registration_revision_history",
            "hub_git_remote_registration_revisions",
            ["registration_id", "revision"],
        )

    existing = set(sa.inspect(bind).get_table_names())
    if "hub_git_remote_registration_audits" not in existing:
        op.create_table(
            "hub_git_remote_registration_audits",
            sa.Column("audit_id", sa.String(64), primary_key=True),
            sa.Column("registration_id", sa.String(64), nullable=False),
            sa.Column("tenant_id", sa.String(191), nullable=False),
            sa.Column("project_id", sa.String(191), nullable=False),
            sa.Column("owner_id", sa.String(191), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column(
                "previous_authorization_state",
                sa.String(32),
                nullable=True,
            ),
            sa.Column("authorization_state", sa.String(32), nullable=False),
            sa.Column("reason_code", sa.String(192), nullable=False),
            sa.Column("actor_id", sa.String(192), nullable=False),
            sa.Column("registration_digest", sa.String(64), nullable=False),
            sa.Column("occurred_at_epoch", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(
                ["registration_id"],
                ["hub_git_remote_registrations.registration_id"],
                name="fk_hub_git_remote_audit_registration",
            ),
        )
        op.create_index(
            "ix_hub_git_remote_registration_audit_scope",
            "hub_git_remote_registration_audits",
            ["tenant_id", "project_id", "owner_id", "occurred_at_epoch"],
        )
        op.create_index(
            "ix_hub_git_remote_registration_audit_registration",
            "hub_git_remote_registration_audits",
            ["registration_id", "revision"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "hub_git_remote_registration_audits" in existing:
        op.drop_table("hub_git_remote_registration_audits")
    existing = set(sa.inspect(bind).get_table_names())
    if "hub_git_remote_registration_revisions" in existing:
        op.drop_table("hub_git_remote_registration_revisions")
    existing = set(sa.inspect(bind).get_table_names())
    if "hub_git_remote_registrations" in existing:
        op.drop_table("hub_git_remote_registrations")
