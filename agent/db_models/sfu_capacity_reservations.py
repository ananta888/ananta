from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


_RESOURCE_COLUMNS = (
    "cpu_millicores",
    "memory_bytes",
    "fd_count",
    "ingress_bps",
    "egress_bps",
    "receivers",
    "tracks",
    "turn_bps",
)


class SfuCapacityLedgerDB(SQLModel, table=True):
    """Atomic aggregate for one cluster/region and optional tenant scope."""

    __tablename__ = "sfu_capacity_ledgers"
    __table_args__ = (
        sa.UniqueConstraint(
            "cluster_id", "region", "tenant_scope", name="uq_sfu_capacity_ledger_scope"
        ),
        *(sa.CheckConstraint(f"{name} >= 0", name=f"ck_sfu_capacity_ledger_{name}") for name in _RESOURCE_COLUMNS),
        sa.CheckConstraint("version > 0", name="ck_sfu_capacity_ledger_version"),
    )

    id: str = Field(primary_key=True)
    cluster_id: str = Field(index=True)
    region: str = Field(index=True)
    tenant_scope: str = Field(index=True)
    cpu_millicores: int = 0
    memory_bytes: int = 0
    fd_count: int = 0
    ingress_bps: int = 0
    egress_bps: int = 0
    receivers: int = 0
    tracks: int = 0
    turn_bps: int = 0
    version: int = 1
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuCapacityReservationDB(SQLModel, table=True):
    """Hub-owned, leased room capacity reservation."""

    __tablename__ = "sfu_capacity_reservations"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "room_id", name="uq_sfu_capacity_reservation_room"),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'expired')",
            name="ck_sfu_capacity_reservation_status",
        ),
        *(sa.CheckConstraint(f"{name} >= 0", name=f"ck_sfu_capacity_reservation_{name}") for name in _RESOURCE_COLUMNS),
        sa.CheckConstraint("directory_version > 0", name="ck_sfu_capacity_directory_version"),
        sa.CheckConstraint("fencing_token > 0", name="ck_sfu_capacity_fencing_positive"),
        sa.CheckConstraint("version > 0", name="ck_sfu_capacity_reservation_version"),
        sa.CheckConstraint("lease_expires_at >= created_at", name="ck_sfu_capacity_lease_order"),
        sa.Index(
            "ix_sfu_capacity_reservation_scope_lease",
            "cluster_id",
            "region",
            "status",
            "lease_expires_at",
        ),
        sa.Index("ix_sfu_capacity_reservation_node", "observed_node_id", "status"),
    )

    id: str = Field(
        default_factory=lambda: f"sfu-capacity-{uuid.uuid4().hex}", primary_key=True
    )
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    cluster_id: str = Field(index=True)
    region: str = Field(index=True)
    runtime_control_mode: str
    placement_owner: str
    observed_node_id: str | None = Field(default=None, index=True)
    runtime_instance_id: str | None = Field(default=None, index=True)
    infrastructure_profile_id: str = Field(index=True)
    slo_profile_id: str = Field(index=True)
    cpu_millicores: int
    memory_bytes: int
    fd_count: int
    ingress_bps: int
    egress_bps: int
    receivers: int
    tracks: int
    turn_bps: int
    lease_expires_at: float = Field(index=True)
    directory_version: int
    fencing_token: int = Field(index=True)
    version: int = Field(index=True)
    status: str = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuCapacityReservationMutationDB(SQLModel, table=True):
    """Idempotency receipt for every capacity mutation."""

    __tablename__ = "sfu_capacity_reservation_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "command_id_digest", name="uq_sfu_capacity_mutation_command"
        ),
        sa.CheckConstraint(
            "length(command_id_digest) = 64 AND length(request_digest) = 64",
            name="ck_sfu_capacity_mutation_digest_length",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"sfu-capacity-mutation-{uuid.uuid4().hex}",
        primary_key=True,
    )
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    reservation_id: str = Field(index=True)
    operation: str = Field(index=True)
    command_id_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    result_json: dict = Field(sa_column=Column(JSON, nullable=False), repr=False)
    created_at: float = Field(default_factory=time.time, index=True)
    expires_at: float | None = Field(default=None, index=True)


__all__ = [
    "SfuCapacityLedgerDB",
    "SfuCapacityReservationDB",
    "SfuCapacityReservationMutationDB",
]
