"""add source control public remotes

Revision ID: 4b9e2a7c0d3f
Revises: 3a8d1f6b9c2e
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "4b9e2a7c0d3f"
down_revision = "3a8d1f6b9c2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_control_public_remote_validations",
        sa.Column("handle_digest", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("owner_id", sa.String(191), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("repository_path", sa.String(512), nullable=False),
        sa.Column("requested_ref", sa.String(240), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("expires_at_epoch", sa.Float(), nullable=False),
        sa.Column("consumed_at_epoch", sa.Float(), nullable=True),
        sa.Column("remote_id", sa.String(96), nullable=True),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "provider IN ('github_public', 'https_git')",
            name="ck_source_public_remote_validation_provider",
        ),
    )
    for name in ("tenant_id", "project_id", "owner_id", "expires_at_epoch"):
        op.create_index(
            f"ix_source_control_public_remote_validations_{name}",
            "source_control_public_remote_validations",
            [name],
        )
    op.create_index(
        "ix_source_public_remote_validation_scope",
        "source_control_public_remote_validations",
        ["tenant_id", "project_id", "owner_id", "expires_at_epoch"],
    )

    op.create_table(
        "source_control_public_remotes",
        sa.Column("remote_id", sa.String(96), primary_key=True),
        sa.Column("handle_digest", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("owner_id", sa.String(191), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("repository_path", sa.String(512), nullable=False),
        sa.Column("requested_ref", sa.String(240), nullable=False),
        sa.Column("validated_commit_sha", sa.String(64), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "provider IN ('github_public', 'https_git')",
            name="ck_source_public_remote_provider",
        ),
        sa.UniqueConstraint(
            "handle_digest",
            name="uq_source_public_remote_handle",
        ),
    )
    for name in ("handle_digest", "tenant_id", "project_id", "owner_id"):
        op.create_index(
            f"ix_source_control_public_remotes_{name}",
            "source_control_public_remotes",
            [name],
        )
    op.create_index(
        "ix_source_public_remote_scope",
        "source_control_public_remotes",
        ["tenant_id", "project_id", "owner_id", "remote_id"],
    )

    op.create_table(
        "source_control_public_remote_audits",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("actor_id", sa.String(191), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("occurred_at_epoch", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('allow', 'deny')",
            name="ck_source_public_remote_audit_decision",
        ),
    )
    for name in ("tenant_id", "project_id", "actor_id"):
        op.create_index(
            f"ix_source_control_public_remote_audits_{name}",
            "source_control_public_remote_audits",
            [name],
        )
    op.create_index(
        "ix_source_public_remote_audit_scope",
        "source_control_public_remote_audits",
        ["tenant_id", "project_id", "actor_id", "occurred_at_epoch"],
    )


def downgrade() -> None:
    op.drop_table("source_control_public_remote_audits")
    op.drop_table("source_control_public_remotes")
    op.drop_table("source_control_public_remote_validations")
