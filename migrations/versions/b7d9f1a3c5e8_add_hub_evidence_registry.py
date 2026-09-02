"""Add Hub-owned source and run evidence identity registry.

Revision ID: b7d9f1a3c5e8
Revises: a4c6e8f0b2d5
"""

import sqlalchemy as sa
from alembic import op

revision = "b7d9f1a3c5e8"
down_revision = "a4c6e8f0b2d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_source_evidence_identities",
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("source_id", sa.String(192), nullable=False),
        sa.Column("origin_type", sa.String(64), nullable=False),
        sa.Column("origin_digest", sa.String(64), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("evidence_scope", sa.String(32), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("issuer", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "project_id", "source_id"),
        sa.UniqueConstraint("binding_digest"),
        sa.CheckConstraint(
            "evidence_scope IN ('test','local','external','production')",
            name="ck_hub_source_evidence_scope",
        ),
        sa.CheckConstraint(
            "state IN ('admitted','revoked')",
            name="ck_hub_source_evidence_state",
        ),
    )
    op.create_index(
        "ix_hub_source_evidence_binding_digest",
        "hub_source_evidence_identities",
        ["binding_digest"],
    )
    op.create_index(
        "ix_hub_source_evidence_scope",
        "hub_source_evidence_identities",
        ["tenant_id", "project_id", "evidence_scope", "state"],
    )

    op.create_table(
        "hub_run_evidence_identities",
        sa.Column("tenant_id", sa.String(191), nullable=False),
        sa.Column("project_id", sa.String(191), nullable=False),
        sa.Column("run_id", sa.String(192), nullable=False),
        sa.Column("task_id", sa.String(191), nullable=False),
        sa.Column("assignment_id", sa.String(191), nullable=False),
        sa.Column("dispatch_lease_id", sa.String(191), nullable=False),
        sa.Column("repository_revision", sa.String(64), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("execution_profile_digest", sa.String(64), nullable=False),
        sa.Column("environment_digest", sa.String(64), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_scope", sa.String(32), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("issuer", sa.String(128), nullable=False),
        sa.Column("reservation_key_digest", sa.String(64), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "project_id", "run_id"),
        sa.UniqueConstraint("reservation_key_digest"),
        sa.UniqueConstraint("binding_digest"),
        sa.CheckConstraint(
            "evidence_scope IN ('test','local','external','production')",
            name="ck_hub_run_evidence_scope",
        ),
        sa.CheckConstraint(
            "state IN ('reserved','succeeded','failed','cancelled')",
            name="ck_hub_run_evidence_state",
        ),
    )
    op.create_index(
        "ix_hub_run_evidence_reservation_key_digest",
        "hub_run_evidence_identities",
        ["reservation_key_digest"],
    )
    op.create_index(
        "ix_hub_run_evidence_binding_digest",
        "hub_run_evidence_identities",
        ["binding_digest"],
    )
    op.create_index(
        "ix_hub_run_evidence_scope",
        "hub_run_evidence_identities",
        ["tenant_id", "project_id", "task_id", "state"],
    )
    op.create_index(
        "ix_hub_run_evidence_assignment_id",
        "hub_run_evidence_identities",
        ["assignment_id"],
    )
    op.create_index(
        "ix_hub_run_evidence_dispatch_lease_id",
        "hub_run_evidence_identities",
        ["dispatch_lease_id"],
    )


def downgrade() -> None:
    op.drop_table("hub_run_evidence_identities")
    op.drop_table("hub_source_evidence_identities")
