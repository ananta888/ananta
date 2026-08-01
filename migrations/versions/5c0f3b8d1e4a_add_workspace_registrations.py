"""add source control workspace registrations

Revision ID: 5c0f3b8d1e4a
Revises: 4b9e2a7c0d3f
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "5c0f3b8d1e4a"
down_revision = "4b9e2a7c0d3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_control_workspace_validations",
        sa.Column("handle_digest", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("owner_id", sa.String(191), nullable=False),
        sa.Column("folder_handle", sa.String(96), nullable=False),
        sa.Column("root_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("expires_at_epoch", sa.Float(), nullable=False),
        sa.Column("consumed_at_epoch", sa.Float(), nullable=True),
        sa.Column("workspace_id", sa.String(96), nullable=True),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
    )
    for name in ("tenant_id", "project_id", "owner_id", "expires_at_epoch"):
        op.create_index(
            f"ix_source_control_workspace_validations_{name}",
            "source_control_workspace_validations",
            [name],
        )
    op.create_index(
        "ix_source_workspace_validation_scope",
        "source_control_workspace_validations",
        ["tenant_id", "project_id", "owner_id", "expires_at_epoch"],
    )

    op.create_table(
        "source_control_workspace_registrations",
        sa.Column("workspace_id", sa.String(96), primary_key=True),
        sa.Column(
            "validation_handle_digest",
            sa.String(64),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("owner_id", sa.String(191), nullable=False),
        sa.Column("folder_handle", sa.String(96), nullable=False),
        sa.Column("root_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("registration_state", sa.String(16), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "registration_state IN ('active', 'disabled')",
            name="ck_source_workspace_registration_state",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "folder_handle",
            name="uq_source_workspace_registration_folder",
        ),
        sa.UniqueConstraint(
            "validation_handle_digest",
            name="uq_sc_workspace_reg_validation",
        ),
    )
    op.create_index(
        "ix_sc_workspace_reg_validation",
        "source_control_workspace_registrations",
        ["validation_handle_digest"],
    )
    for name in ("tenant_id", "project_id", "owner_id"):
        op.create_index(
            f"ix_source_control_workspace_registrations_{name}",
            "source_control_workspace_registrations",
            [name],
        )
    op.create_index(
        "ix_source_workspace_registration_scope",
        "source_control_workspace_registrations",
        ["tenant_id", "project_id", "owner_id", "workspace_id"],
    )

    op.create_table(
        "source_control_workspace_registration_audits",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("actor_id", sa.String(191), nullable=False),
        sa.Column("workspace_id_digest", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("occurred_at_epoch", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('allow', 'deny')",
            name="ck_source_workspace_registration_audit_decision",
        ),
    )
    for name in ("tenant_id", "project_id", "actor_id"):
        op.create_index(
            f"ix_source_control_workspace_registration_audits_{name}",
            "source_control_workspace_registration_audits",
            [name],
        )
    op.create_index(
        "ix_source_workspace_registration_audit_scope",
        "source_control_workspace_registration_audits",
        ["tenant_id", "project_id", "actor_id", "occurred_at_epoch"],
    )


def downgrade() -> None:
    op.drop_table("source_control_workspace_registration_audits")
    op.drop_table("source_control_workspace_registrations")
    op.drop_table("source_control_workspace_validations")
