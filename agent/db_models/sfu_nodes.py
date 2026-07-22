from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class SfuNodeDB(SQLModel, table=True):
    """Persistent Hub directory entry for one enrolled SFU runtime."""

    __tablename__ = "sfu_nodes"
    __table_args__ = (
        sa.UniqueConstraint("node_id", name="uq_sfu_nodes_node_id"),
        sa.UniqueConstraint("runtime_identity_id", name="uq_sfu_nodes_runtime_identity_id"),
        sa.CheckConstraint("version > 0", name="ck_sfu_nodes_version_positive"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_sfu_nodes_fencing_non_negative"),
        sa.Index("ix_sfu_nodes_scope_sort", "tenant_id", "cluster_id", "node_id", "id"),
        sa.Index(
            "ix_sfu_nodes_scope_observation",
            "tenant_id",
            "cluster_id",
            "observation_expires_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"sfu-node-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    cluster_id: str = Field(index=True)
    node_id: str = Field(index=True)
    runtime_identity_id: str = Field(index=True)
    enrollment_status: str = Field(default="enrolled", index=True)
    region: str = Field(index=True)
    adapter_name: str
    adapter_version: str
    protocol_version: str
    capability_digest: str = Field(index=True)
    last_observation_id: str | None = Field(default=None, index=True)
    last_observed_at: float | None = Field(default=None, index=True)
    observation_expires_at: float | None = Field(default=None, index=True)
    health_status: str = Field(default="unknown", index=True)
    drain_state: str = Field(default="active", index=True)
    drain_reason: str | None = None
    drain_requested_at: float | None = Field(default=None, index=True)
    drained_at: float | None = None
    revoked_at: float | None = Field(default=None, index=True)
    revocation_reason: str | None = None
    fencing_token: int = Field(default=0, index=True)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuNodeMutationDB(SQLModel, table=True):
    """Append-only change record used as a durable, ordered watch stream."""

    __tablename__ = "sfu_node_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "cluster_id",
            "node_id",
            "node_version",
            name="uq_sfu_node_mutations_scope_node_version",
        ),
        sa.Index(
            "ix_sfu_node_mutations_scope_sequence",
            "tenant_id",
            "cluster_id",
            "sequence",
        ),
    )

    sequence: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    cluster_id: str = Field(index=True)
    node_id: str = Field(index=True)
    node_version: int = Field(index=True)
    fencing_token: int
    event_type: str = Field(index=True)
    snapshot_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    occurred_at: float = Field(default_factory=time.time, index=True)


__all__ = ["SfuNodeDB", "SfuNodeMutationDB"]
