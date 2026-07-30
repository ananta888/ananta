"""Add persistent Context Policy lifecycle records.

Revision ID: 0d5f7b9c1e3a
Revises: fc4e6a8b0d2f
"""

from alembic import op
import sqlalchemy as sa


revision = "0d5f7b9c1e3a"
down_revision = "fc4e6a8b0d2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_control_context_policy_versions",
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("policy_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("etag", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("record_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "policy_id",
            "version",
            name="uq_sc_context_policy_scope_version",
        ),
    )
    for name, columns in (
        (
            "ix_sc_context_policy_version_tenant",
            ["tenant_id"],
        ),
        (
            "ix_sc_context_policy_version_project",
            ["project_id"],
        ),
        (
            "ix_sc_context_policy_version_policy",
            ["policy_id"],
        ),
        (
            "ix_sc_context_policy_version_state",
            ["state"],
        ),
        (
            "ix_sc_context_policy_version_digest",
            ["policy_digest"],
        ),
        (
            "ix_sc_context_policy_version_etag",
            ["etag"],
        ),
    ):
        op.create_index(
            name,
            "source_control_context_policy_versions",
            columns,
        )
    op.create_index(
        "uq_sc_context_policy_active_scope",
        "source_control_context_policy_versions",
        ["tenant_id", "project_id", "policy_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "source_control_context_policy_mutations",
        sa.Column("mutation_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("policy_id", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column(
            "idempotency_key",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("result_etag", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("mutation_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "policy_id",
            "operation",
            "idempotency_key",
            name="uq_sc_context_policy_mutation_key",
        ),
    )
    for name, columns in (
        ("ix_sc_context_policy_mutation_tenant", ["tenant_id"]),
        ("ix_sc_context_policy_mutation_project", ["project_id"]),
        ("ix_sc_context_policy_mutation_policy", ["policy_id"]),
        ("ix_sc_context_policy_mutation_operation", ["operation"]),
    ):
        op.create_index(
            name,
            "source_control_context_policy_mutations",
            columns,
        )

    op.create_table(
        "source_control_context_policy_audit",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("policy_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    for name, columns in (
        ("ix_sc_context_policy_audit_operation", ["operation"]),
        ("ix_sc_context_policy_audit_actor", ["actor_id"]),
        ("ix_sc_context_policy_audit_tenant", ["tenant_id"]),
        ("ix_sc_context_policy_audit_project", ["project_id"]),
        ("ix_sc_context_policy_audit_policy", ["policy_id"]),
    ):
        op.create_index(
            name,
            "source_control_context_policy_audit",
            columns,
        )


def downgrade() -> None:
    op.drop_table("source_control_context_policy_audit")
    op.drop_table("source_control_context_policy_mutations")
    op.drop_index(
        "uq_sc_context_policy_active_scope",
        table_name="source_control_context_policy_versions",
    )
    op.drop_table("source_control_context_policy_versions")
