"""add HRM mutation idempotency receipts

Revision ID: e5a7b9d1f3c6
Revises: d4f6a8c0e2b6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e5a7b9d1f3c6"
down_revision = "d4f6a8c0e2b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "hrm_idempotency_receipts" not in set(inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "hrm_idempotency_receipts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("resource_id", sa.String(), nullable=True),
            sa.Column("response", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "owner_subject", "operation", "key_digest",
                name="uq_hrm_idempotency_receipt_scope",
            ),
        )
    op.create_index(
        "ix_hrm_idempotency_receipt_resource",
        "hrm_idempotency_receipts",
        ["tenant_id", "operation", "resource_id"],
        unique=False,
        if_not_exists=True,
    )
    for column in (
        "tenant_id",
        "owner_subject",
        "operation",
        "key_digest",
        "request_digest",
        "state",
        "resource_id",
        "created_at",
    ):
        op.create_index(
            f"ix_hrm_idempotency_receipts_{column}",
            "hrm_idempotency_receipts",
            [column],
            unique=False,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.drop_table("hrm_idempotency_receipts")
