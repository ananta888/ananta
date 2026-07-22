"""Add durable SFU group-key, opaque identity and retention state.

Revision ID: 6e05f6a7b8c9
Revises: 5df4e5f6a7b8
Create Date: 2026-07-22 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "6e05f6a7b8c9"
down_revision: str | Sequence[str] | None = "5df4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_audience_snapshot_tombstones" not in existing:
        op.create_table(
            "sfu_audience_snapshot_tombstones",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scope_digest", sa.String(), nullable=False),
            sa.Column("final_version", sa.Integer(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("purged_at", sa.Float(), nullable=False),
            sa.Column("deny_until", sa.Float(), nullable=False),
            sa.CheckConstraint("final_version > 0 AND fencing_token > 0", name="ck_sfu_audience_tombstone_fence_version"),
            sa.CheckConstraint("deny_until >= purged_at", name="ck_sfu_audience_tombstone_ttl"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_sfu_audience_tombstone_retention", "sfu_audience_snapshot_tombstones", ["deny_until", "purged_at"])
        op.create_index("ix_sfu_audience_snapshot_tombstones_scope_digest", "sfu_audience_snapshot_tombstones", ["scope_digest"])

    if "sfu_audience_retention_fences" not in existing:
        op.create_table(
            "sfu_audience_retention_fences",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("lease_expires_at", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_audience_retention_fence"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "sfu_broadcast_group_key_authorizations" not in existing:
        op.create_table(
            "sfu_broadcast_group_key_authorizations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("publication_id", sa.String(), nullable=False),
            sa.Column("publisher_digest", sa.String(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("previous_key_epoch", sa.Integer(), nullable=False),
            sa.Column("member_set_digest", sa.String(), nullable=False),
            sa.Column("authorization_json", sa.JSON(), nullable=False),
            sa.Column("distribution_mode", sa.String(), nullable=False),
            sa.Column("package_count", sa.Integer(), nullable=False),
            sa.Column("total_package_bytes", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("valid_from_ms", sa.Integer(), nullable=False),
            sa.Column("expires_at_ms", sa.Integer(), nullable=False),
            sa.Column("rekey_deadline_ms", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("tombstoned_at", sa.Float(), nullable=True),
            sa.CheckConstraint("membership_epoch > 0 AND key_epoch > 0", name="ck_sfu_group_key_epochs"),
            sa.CheckConstraint("previous_key_epoch >= 0 AND previous_key_epoch < key_epoch", name="ck_sfu_group_key_transition"),
            sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_group_key_fence_version"),
            sa.CheckConstraint("package_count >= 0 AND total_package_bytes >= 0", name="ck_sfu_group_key_bounds"),
            sa.CheckConstraint("distribution_mode = 'bounded_rewrap'", name="ck_sfu_group_key_distribution_mode"),
            sa.CheckConstraint("status IN ('active','revoked','expired','tombstoned')", name="ck_sfu_group_key_status"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "room_id", "publication_id", "key_epoch", name="uq_sfu_group_key_authorization_epoch"),
        )
        op.create_index(
            "uq_sfu_group_key_authorization_active",
            "sfu_broadcast_group_key_authorizations",
            ["tenant_id", "room_id", "publication_id"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )
        op.create_index("ix_sfu_group_key_authorization_expiry", "sfu_broadcast_group_key_authorizations", ["status", "expires_at_ms", "updated_at"])
        for name in ("tenant_id", "session_id", "room_id", "publication_id", "publisher_digest", "membership_epoch", "key_epoch", "status", "fencing_token", "version", "expires_at_ms"):
            op.create_index(f"ix_sfu_broadcast_group_key_authorizations_{name}", "sfu_broadcast_group_key_authorizations", [name])

    if "sfu_broadcast_group_key_packages" not in existing:
        op.create_table(
            "sfu_broadcast_group_key_packages",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("authorization_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("recipient_digest", sa.String(), nullable=False),
            sa.Column("package_digest", sa.String(), nullable=False),
            sa.Column("package_bytes", sa.Integer(), nullable=False),
            sa.Column("sealed_package", sa.LargeBinary(), nullable=True),
            sa.Column("wrapping_nonce", sa.LargeBinary(), nullable=True),
            sa.Column("wrapping_key_id", sa.String(), nullable=True),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("expires_at_ms", sa.Integer(), nullable=False),
            sa.Column("delivered_at", sa.Float(), nullable=False),
            sa.Column("acknowledged_at", sa.Float(), nullable=True),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("membership_epoch > 0 AND key_epoch > 0", name="ck_sfu_group_key_package_epochs"),
            sa.CheckConstraint("package_bytes > 0 AND version > 0 AND fencing_token > 0", name="ck_sfu_group_key_package_bounds"),
            sa.CheckConstraint("status IN ('delivered','acknowledged','revoked','expired','tombstoned')", name="ck_sfu_group_key_package_status"),
            sa.ForeignKeyConstraint(["authorization_id"], ["sfu_broadcast_group_key_authorizations.id"], name="fk_sfu_group_key_package_authorization", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("authorization_id", "recipient_digest", name="uq_sfu_group_key_package_recipient"),
        )
        op.create_index("ix_sfu_group_key_package_delivery", "sfu_broadcast_group_key_packages", ["tenant_id", "session_id", "recipient_digest", "status", "expires_at_ms"])
        for name in ("authorization_id", "tenant_id", "session_id", "recipient_digest", "status", "expires_at_ms"):
            op.create_index(f"ix_sfu_broadcast_group_key_packages_{name}", "sfu_broadcast_group_key_packages", [name])

    if "sfu_broadcast_group_key_receipts" not in existing:
        op.create_table(
            "sfu_broadcast_group_key_receipts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("actor_digest", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("idempotency_key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("expires_at_ms", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.CheckConstraint("operation IN ('prepare','deliver')", name="ck_sfu_group_key_receipt_operation"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "actor_digest", "operation", "idempotency_key_digest", name="uq_sfu_group_key_receipt_idempotency"),
        )
        op.create_index("ix_sfu_group_key_receipt_expiry", "sfu_broadcast_group_key_receipts", ["expires_at_ms", "created_at"])
        for name in ("tenant_id", "actor_digest", "operation", "expires_at_ms"):
            op.create_index(f"ix_sfu_broadcast_group_key_receipts_{name}", "sfu_broadcast_group_key_receipts", [name])

    if "sfu_broadcast_vendor_identities" not in existing:
        op.create_table(
            "sfu_broadcast_vendor_identities",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("membership_digest", sa.String(), nullable=False),
            sa.Column("membership_ciphertext", sa.LargeBinary(), nullable=True),
            sa.Column("membership_nonce", sa.LargeBinary(), nullable=True),
            sa.Column("wrapping_key_id", sa.String(), nullable=True),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("identity_epoch", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("issued_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("membership_epoch > 0 AND identity_epoch > 0", name="ck_sfu_vendor_identity_epochs"),
            sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_vendor_identity_fence_version"),
            sa.CheckConstraint("expires_at > issued_at", name="ck_sfu_vendor_identity_ttl"),
            sa.CheckConstraint("status IN ('active','revoked','expired','tombstoned')", name="ck_sfu_vendor_identity_status"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "uq_sfu_vendor_identity_active_membership",
            "sfu_broadcast_vendor_identities",
            ["tenant_id", "room_id", "membership_digest", "membership_epoch", "identity_epoch"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )
        op.create_index("ix_sfu_vendor_identity_expiry", "sfu_broadcast_vendor_identities", ["status", "expires_at", "updated_at"])
        for name in ("tenant_id", "room_id", "membership_digest", "membership_epoch", "identity_epoch", "status", "fencing_token", "version", "expires_at", "updated_at"):
            op.create_index(f"ix_sfu_broadcast_vendor_identities_{name}", "sfu_broadcast_vendor_identities", [name])

    if "sfu_broadcast_destination_handles" not in existing:
        op.create_table(
            "sfu_broadcast_destination_handles",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("identity_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("route_digest", sa.String(), nullable=False),
            sa.Column("publication_digest", sa.String(), nullable=False),
            sa.Column("audience_digest", sa.String(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("identity_epoch", sa.Integer(), nullable=False),
            sa.Column("route_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("issued_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("membership_epoch > 0 AND identity_epoch > 0 AND route_epoch > 0 AND key_epoch > 0", name="ck_sfu_destination_epochs"),
            sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_destination_fence_version"),
            sa.CheckConstraint("status IN ('active','revoked','expired','tombstoned')", name="ck_sfu_destination_status"),
            sa.ForeignKeyConstraint(["identity_id"], ["sfu_broadcast_vendor_identities.id"], name="fk_sfu_destination_identity", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("identity_id", "route_digest", "publication_digest", "audience_digest", "route_epoch", "key_epoch", name="uq_sfu_destination_authorized_intent"),
        )
        op.create_index("ix_sfu_destination_expiry", "sfu_broadcast_destination_handles", ["status", "expires_at", "updated_at"])
        for name in ("identity_id", "tenant_id", "room_id", "route_digest", "publication_digest", "audience_digest", "status", "fencing_token", "expires_at", "updated_at"):
            op.create_index(f"ix_sfu_broadcast_destination_handles_{name}", "sfu_broadcast_destination_handles", [name])


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "sfu_broadcast_group_key_authorizations",
        "sfu_broadcast_vendor_identities",
    ):
        if table in existing:
            active = op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} WHERE status = 'active' LIMIT 1")).first()
            if active is not None:
                raise RuntimeError(f"refusing to drop active state from {table}")
    for table in (
        "sfu_broadcast_destination_handles",
        "sfu_broadcast_vendor_identities",
        "sfu_broadcast_group_key_receipts",
        "sfu_broadcast_group_key_packages",
        "sfu_broadcast_group_key_authorizations",
        "sfu_audience_retention_fences",
        "sfu_audience_snapshot_tombstones",
    ):
        if table in existing:
            op.drop_table(table)
