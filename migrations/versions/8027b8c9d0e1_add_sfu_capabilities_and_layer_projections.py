"""add bounded browser capabilities and SFU layer projections

Revision ID: 8027b8c9d0e1
Revises: 7f16a7b8c9d0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "8027b8c9d0e1"
down_revision: str | Sequence[str] | None = "7f16a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "sfu_browser_capabilities" not in existing:
        op.create_table(
            "sfu_browser_capabilities",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("browser_pseudonym", sa.String(), nullable=False),
            sa.Column("admission_epoch", sa.Integer(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("capability_version", sa.String(), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("capability_class", sa.String(), nullable=False),
            sa.Column("buckets_json", sa.JSON(), nullable=False),
            sa.Column("document_digest", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("retain_until_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("tenant_id", "room_id", "browser_pseudonym", name="uq_sfu_browser_capability_scope"),
            sa.CheckConstraint("admission_epoch > 0 AND membership_epoch > 0", name="ck_sfu_browser_capability_epochs"),
            sa.CheckConstraint("sequence > 0 AND version > 0", name="ck_sfu_browser_capability_sequence"),
            sa.CheckConstraint("status IN ('active','unsupported','revoked')", name="ck_sfu_browser_capability_status"),
        )
        op.create_index("ix_sfu_browser_capability_active", "sfu_browser_capabilities", ["tenant_id", "room_id", "status", "expires_at_ms"])
        op.create_index("ix_sfu_browser_capability_retain", "sfu_browser_capabilities", ["retain_until_ms"])
    if "sfu_layer_projections" not in existing:
        op.create_table(
            "sfu_layer_projections",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("projection_kind", sa.String(), nullable=False),
            sa.Column("subject_ref", sa.String(), nullable=False),
            sa.Column("projection_ref", sa.String(), nullable=False),
            sa.Column("projection_version", sa.Integer(), nullable=False),
            sa.Column("session_projection_version", sa.Integer(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("route_epoch", sa.Integer(), nullable=False),
            sa.Column("topology_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("expected_previous_version", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("payload_digest", sa.String(), nullable=False),
            sa.Column("signature", sa.String(), nullable=False),
            sa.Column("signature_key_id", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("retain_until_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("tenant_id", "room_id", "projection_kind", "subject_ref", name="uq_sfu_layer_projection_scope"),
            sa.CheckConstraint("projection_kind IN ('room','publisher','receiver')", name="ck_sfu_layer_projection_kind"),
            sa.CheckConstraint("projection_version > 0 AND fencing_token > 0", name="ck_sfu_layer_projection_version"),
            sa.CheckConstraint("membership_epoch > 0", name="ck_sfu_layer_projection_membership_epoch"),
            sa.CheckConstraint("status IN ('active','revoked','expired')", name="ck_sfu_layer_projection_status"),
        )
        op.create_index("ix_sfu_layer_projection_current", "sfu_layer_projections", ["tenant_id", "room_id", "projection_kind", "status", "expires_at_ms"])
        op.create_index("ix_sfu_layer_projection_retain", "sfu_layer_projections", ["retain_until_ms"])
    if "sfu_layer_projection_receipts" not in existing:
        op.create_table(
            "sfu_layer_projection_receipts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("projection_ref", sa.String(), nullable=False),
            sa.Column("actor_digest", sa.String(), nullable=False),
            sa.Column("receipt_sequence", sa.Integer(), nullable=False),
            sa.Column("receipt_json", sa.JSON(), nullable=False),
            sa.Column("receipt_digest", sa.String(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint("tenant_id", "projection_ref", "actor_digest", "receipt_sequence", name="uq_sfu_layer_projection_receipt_sequence"),
            sa.CheckConstraint("receipt_sequence > 0", name="ck_sfu_layer_projection_receipt_sequence"),
        )
        op.create_index("ix_sfu_layer_projection_receipt_retention", "sfu_layer_projection_receipts", ["projection_ref", "expires_at_ms"])


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("sfu_layer_projection_receipts", "sfu_layer_projections", "sfu_browser_capabilities"):
        if table in existing:
            op.drop_table(table)
