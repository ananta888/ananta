from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class TurnPoolNodeDB(SQLModel, table=True):
    __tablename__ = "turn_pool_nodes"
    __table_args__ = (
        sa.UniqueConstraint("pool_id", "instance_id", name="uq_turn_pool_node_scope"),
        sa.CheckConstraint("version > 0", name="ck_turn_pool_node_version_positive"),
        sa.Index("ix_turn_pool_node_selection", "region", "status", "health_status", "capacity_status"),
    )

    id: str = Field(default_factory=lambda: f"turn-pool-node-{uuid.uuid4().hex}", primary_key=True)
    pool_id: str = Field(index=True)
    instance_id: str = Field(index=True)
    observer_identity_id: str = Field(index=True)
    region: str = Field(index=True)
    endpoint_urls: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    transports: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    credential_binding_modes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    relay_port_min: int
    relay_port_max: int
    allocation_limit: int
    bps_limit: int
    cost_profile: str = Field(index=True)
    cost_units: float
    certificate_fingerprint: str = Field(index=True)
    config_digest: str = Field(index=True)
    contract_version: int = Field(default=1, index=True)
    config_version: str = Field(default="", index=True)
    observer_identity_version: int = Field(default=1, index=True)
    trust_policy_version: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)
    health_status: str = Field(default="unknown", index=True)
    relay_status: str = Field(default="unknown", index=True)
    capacity_status: str = Field(default="unknown", index=True)
    draining: bool = Field(default=False, index=True)
    observation_fencing_token: int = Field(default=0, index=True)
    observation_version: int = Field(default=0, index=True)
    last_observed_at: float | None = Field(default=None, index=True)
    last_observation_id: str | None = Field(default=None, index=True)
    last_reason_code: str | None = Field(default=None, index=True)
    fresh_until: float | None = Field(default=None, index=True)
    version: int = Field(default=1, index=True)
    revoked_at: float | None = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class TurnPoolNodeMutationDB(SQLModel, table=True):
    __tablename__ = "turn_pool_node_mutations"
    __table_args__ = (
        sa.UniqueConstraint("actor", "idempotency_key_digest", name="uq_turn_pool_mutation_actor_key"),
    )

    id: str = Field(default_factory=lambda: f"turn-pool-mutation-{uuid.uuid4().hex}", primary_key=True)
    pool_id: str = Field(index=True)
    instance_id: str = Field(index=True)
    operation: str = Field(index=True)
    actor: str = Field(index=True)
    expected_version: int
    result_version: int = Field(index=True)
    reason_code: str
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    response_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    audited_at: float = Field(default_factory=time.time, index=True)
    expires_at: float | None = Field(default=None, index=True)


__all__ = ["TurnPoolNodeDB", "TurnPoolNodeMutationDB"]
