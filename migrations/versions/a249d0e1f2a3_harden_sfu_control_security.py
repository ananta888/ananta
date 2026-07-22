"""Harden SFU control security and bounded persistence.

Revision ID: a249d0e1f2a3
Revises: 9138c9d0e1f2
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a249d0e1f2a3"
down_revision = "9138c9d0e1f2"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table: str, column: sa.Column) -> None:
    if table in _tables() and column.name not in _columns(table):
        op.add_column(table, column)


def _index(table: str, name: str, columns: list[str]) -> None:
    if table not in _tables():
        return
    names = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in names and set(columns) <= _columns(table):
        op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    ttl_tables = (
        "turn_observer_identity_mutations",
        "turn_observer_enrollment_rate_limits",
        "turn_pool_node_mutations",
        "sfu_capacity_reservation_mutations",
    )
    for table in ttl_tables:
        _add(table, sa.Column("expires_at", sa.Float(), nullable=True))
        _index(table, f"ix_{table}_expires_at", ["expires_at"])

    for name, kind in (
        ("result_region", sa.String(128)),
        ("result_role", sa.String(128)),
        ("result_audience", sa.String(128)),
        ("result_recovery_evidence_required", sa.Boolean()),
    ):
        _add("turn_observer_identity_mutations", sa.Column(name, kind, nullable=True))

    _add("turn_pool_nodes", sa.Column("contract_version", sa.Integer(), nullable=False, server_default="1"))
    _add("turn_pool_nodes", sa.Column("config_version", sa.String(128), nullable=False, server_default=""))
    _add("turn_pool_nodes", sa.Column("observer_identity_version", sa.Integer(), nullable=False, server_default="1"))
    _add("turn_pool_nodes", sa.Column("trust_policy_version", sa.String(128), nullable=False, server_default=""))
    _add("turn_pool_nodes", sa.Column("last_observation_id", sa.String(128), nullable=True))
    _add("turn_pool_nodes", sa.Column("last_reason_code", sa.String(128), nullable=True))

    _add("sfu_broadcast_group_key_packages", sa.Column("recipient_digest_key_id", sa.String(128), nullable=True))
    _index(
        "sfu_broadcast_group_key_packages",
        "ix_sfu_group_packages_recipient_key",
        ["tenant_id", "recipient_digest_key_id", "recipient_digest"],
    )
    _add("sfu_broadcast_vendor_identities", sa.Column("membership_digest_key_id", sa.String(128), nullable=True))
    _index(
        "sfu_broadcast_vendor_identities",
        "ix_sfu_vendor_identity_membership_key",
        ["tenant_id", "room_id", "membership_digest_key_id", "membership_digest"],
    )

    for name, kind in (
        ("authorization_ciphertext", sa.LargeBinary()),
        ("authorization_nonce", sa.LargeBinary()),
        ("authorization_wrapping_key_id", sa.String(128)),
    ):
        _add("sfu_broadcast_group_key_authorizations", sa.Column(name, kind, nullable=True))

    _add("sfu_command_idempotency_ledger", sa.Column("operation_id", sa.String(128), nullable=True))
    _add("sfu_command_idempotency_ledger", sa.Column("delivery_state", sa.String(32), nullable=False, server_default="pending"))
    _add("sfu_command_idempotency_ledger", sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"))
    _add("sfu_command_idempotency_ledger", sa.Column("result_code", sa.String(128), nullable=True))
    _add("sfu_command_idempotency_ledger", sa.Column("result_version", sa.Integer(), nullable=True))
    _index("sfu_command_idempotency_ledger", "ix_sfu_command_delivery", ["delivery_state", "expires_at"])


def downgrade() -> None:
    drops = {
        "sfu_command_idempotency_ledger": (
            "result_version", "result_code", "delivery_attempts",
            "delivery_state", "operation_id",
        ),
        "sfu_broadcast_group_key_authorizations": (
            "authorization_wrapping_key_id", "authorization_nonce", "authorization_ciphertext",
        ),
        "sfu_broadcast_vendor_identities": ("membership_digest_key_id",),
        "sfu_broadcast_group_key_packages": ("recipient_digest_key_id",),
        "turn_pool_nodes": (
            "last_reason_code", "last_observation_id", "trust_policy_version",
            "observer_identity_version", "config_version", "contract_version",
        ),
        "sfu_capacity_reservation_mutations": ("expires_at",),
        "turn_pool_node_mutations": ("expires_at",),
        "turn_observer_enrollment_rate_limits": ("expires_at",),
        "turn_observer_identity_mutations": (
            "result_recovery_evidence_required", "result_audience", "result_role",
            "result_region", "expires_at",
        ),
    }
    for table, columns in drops.items():
        if table not in _tables():
            continue
        for column in columns:
            if column in _columns(table):
                with op.batch_alter_table(table) as batch:
                    batch.drop_column(column)
