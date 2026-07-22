"""Add atomic SFU capacity reservation ledgers.

Revision ID: 4cf3d4e5f6a7
Revises: 3bf2c3d4e5f6
Create Date: 2026-07-22 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "4cf3d4e5f6a7"
down_revision: str | Sequence[str] | None = "3bf2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESOURCES = (
    "cpu_millicores",
    "memory_bytes",
    "fd_count",
    "ingress_bps",
    "egress_bps",
    "receivers",
    "tracks",
    "turn_bps",
)


def _resource_columns() -> list[sa.Column]:
    return [sa.Column(name, sa.BigInteger(), nullable=False) for name in _RESOURCES]


def _resource_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(f"{name} >= 0", name=f"ck_{prefix}_{name}")
        for name in _RESOURCES
    ]


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_capacity_ledgers" not in existing:
        op.create_table(
            "sfu_capacity_ledgers",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("tenant_scope", sa.String(), nullable=False),
            *_resource_columns(),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            *_resource_checks("sfu_capacity_ledger"),
            sa.CheckConstraint("version > 0", name="ck_sfu_capacity_ledger_version"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "cluster_id", "region", "tenant_scope",
                name="uq_sfu_capacity_ledger_scope",
            ),
        )
        for name in ("cluster_id", "region", "tenant_scope", "updated_at"):
            op.create_index(f"ix_sfu_capacity_ledgers_{name}", "sfu_capacity_ledgers", [name])

    if "sfu_capacity_reservations" not in existing:
        op.create_table(
            "sfu_capacity_reservations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("runtime_control_mode", sa.String(), nullable=False),
            sa.Column("placement_owner", sa.String(), nullable=False),
            sa.Column("observed_node_id", sa.String(), nullable=True),
            sa.Column("runtime_instance_id", sa.String(), nullable=True),
            sa.Column("infrastructure_profile_id", sa.String(), nullable=False),
            sa.Column("slo_profile_id", sa.String(), nullable=False),
            *_resource_columns(),
            sa.Column("lease_expires_at", sa.Float(), nullable=False),
            sa.Column("directory_version", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            *_resource_checks("sfu_capacity_reservation"),
            sa.CheckConstraint(
                "status IN ('active', 'released', 'expired')",
                name="ck_sfu_capacity_reservation_status",
            ),
            sa.CheckConstraint("directory_version > 0", name="ck_sfu_capacity_directory_version"),
            sa.CheckConstraint("fencing_token > 0", name="ck_sfu_capacity_fencing_positive"),
            sa.CheckConstraint("version > 0", name="ck_sfu_capacity_reservation_version"),
            sa.CheckConstraint("lease_expires_at >= created_at", name="ck_sfu_capacity_lease_order"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "room_id", name="uq_sfu_capacity_reservation_room"),
        )
        op.create_index(
            "ix_sfu_capacity_reservation_scope_lease",
            "sfu_capacity_reservations",
            ["cluster_id", "region", "status", "lease_expires_at"],
        )
        op.create_index(
            "ix_sfu_capacity_reservation_node",
            "sfu_capacity_reservations",
            ["observed_node_id", "status"],
        )
        for name in (
            "tenant_id", "room_id", "cluster_id", "region", "runtime_instance_id",
            "infrastructure_profile_id", "slo_profile_id", "fencing_token",
            "version", "status", "lease_expires_at", "updated_at",
        ):
            op.create_index(
                f"ix_sfu_capacity_reservations_{name}",
                "sfu_capacity_reservations",
                [name],
            )

    if "sfu_capacity_reservation_mutations" not in existing:
        op.create_table(
            "sfu_capacity_reservation_mutations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("reservation_id", sa.String(), nullable=False),
            sa.Column("operation", sa.String(), nullable=False),
            sa.Column("command_id_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.CheckConstraint(
                "length(command_id_digest) = 64 AND length(request_digest) = 64",
                name="ck_sfu_capacity_mutation_digest_length",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "command_id_digest",
                name="uq_sfu_capacity_mutation_command",
            ),
        )
        for name in (
            "tenant_id", "room_id", "reservation_id", "operation",
            "command_id_digest", "created_at",
        ):
            op.create_index(
                f"ix_sfu_capacity_reservation_mutations_{name}",
                "sfu_capacity_reservation_mutations",
                [name],
            )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_capacity_reservations" in existing:
        active = op.get_bind().execute(
            sa.text("SELECT 1 FROM sfu_capacity_reservations WHERE status = 'active' LIMIT 1")
        ).first()
        if active is not None:
            raise RuntimeError("refusing to drop active SFU capacity reservations")
    for table in (
        "sfu_capacity_reservation_mutations",
        "sfu_capacity_reservations",
        "sfu_capacity_ledgers",
    ):
        if table in existing:
            op.drop_table(table)
