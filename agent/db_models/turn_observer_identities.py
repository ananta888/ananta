from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class TurnObserverIdentityDB(SQLModel, table=True):
    """Hub-owned TURN observer identity; private key material has no field."""

    __tablename__ = "turn_observer_identities"
    __table_args__ = (
        sa.UniqueConstraint("pool_id", "instance_id", name="uq_turn_observer_pool_instance"),
        sa.CheckConstraint("version > 0", name="ck_turn_observer_identity_version_positive"),
        sa.Index("ix_turn_observer_identity_status_version", "status", "version"),
    )

    id: str = Field(default_factory=lambda: f"turn-observer-{uuid.uuid4().hex}", primary_key=True)
    pool_id: str = Field(index=True)
    instance_id: str = Field(index=True)
    role: str = Field(default="turn_observer", index=True)
    audience: str = Field(index=True)
    region: str = Field(index=True)
    status: str = Field(default="active", index=True)
    version: int = Field(default=1, index=True)
    active_credential_id: str = Field(index=True)
    previous_credential_id: str | None = Field(default=None, index=True)
    rotation_overlap_until: float | None = Field(default=None, index=True)
    recovery_evidence_required: bool = Field(default=False, index=True)
    enrolled_at: float = Field(default_factory=time.time)
    rotated_at: float | None = None
    revoked_at: float | None = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class TurnObserverCredentialDB(SQLModel, table=True):
    """Public key and certificate metadata for one observer credential."""

    __tablename__ = "turn_observer_credentials"
    __table_args__ = (
        sa.UniqueConstraint("public_key_fingerprint", name="uq_turn_observer_public_key_fingerprint"),
        sa.UniqueConstraint("certificate_fingerprint", name="uq_turn_observer_certificate_fingerprint"),
        sa.UniqueConstraint("proof_nonce_digest", name="uq_turn_observer_proof_nonce"),
        sa.Index("ix_turn_observer_credential_identity_status", "identity_id", "status"),
    )

    id: str = Field(default_factory=lambda: f"turn-observer-credential-{uuid.uuid4().hex}", primary_key=True)
    identity_id: str = Field(foreign_key="turn_observer_identities.id", index=True)
    public_key_b64: str = Field(repr=False)
    public_key_fingerprint: str = Field(index=True)
    certificate_fingerprint: str = Field(index=True)
    ca_fingerprint: str = Field(index=True)
    certificate_san: str = Field(index=True)
    certificate_ekus: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    proof_nonce_digest: str = Field(index=True, repr=False)
    status: str = Field(default="active", index=True)
    valid_from: float = Field(default_factory=time.time)
    valid_until: float = Field(index=True)
    overlap_until: float | None = Field(default=None, index=True)
    revoked_at: float | None = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)


class TurnObserverIdentityMutationDB(SQLModel, table=True):
    """Durable idempotency receipt and content-free security audit."""

    __tablename__ = "turn_observer_identity_mutations"
    __table_args__ = (
        sa.UniqueConstraint("actor", "idempotency_key_digest", name="uq_turn_observer_mutation_actor_key"),
        sa.Index("ix_turn_observer_mutation_scope_version", "pool_id", "instance_id", "result_version"),
    )

    id: str = Field(default_factory=lambda: f"turn-observer-mutation-{uuid.uuid4().hex}", primary_key=True)
    identity_id: str = Field(index=True)
    pool_id: str = Field(index=True)
    instance_id: str = Field(index=True)
    operation: str = Field(index=True)
    expected_version: int
    result_version: int = Field(index=True)
    result_status: str
    result_region: str | None = None
    result_role: str | None = None
    result_audience: str | None = None
    result_recovery_evidence_required: bool | None = None
    actor: str = Field(index=True)
    reason_code: str
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    response_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    audited_at: float = Field(default_factory=time.time, index=True)
    expires_at: float | None = Field(default=None, index=True)


class TurnObserverEnrollmentRateLimitDB(SQLModel, table=True):
    __tablename__ = "turn_observer_enrollment_rate_limits"
    __table_args__ = (
        sa.UniqueConstraint("actor", "source_digest", "window_started_at", name="uq_turn_observer_rate_bucket"),
    )

    id: str = Field(primary_key=True)
    actor: str = Field(index=True)
    source_digest: str = Field(index=True, repr=False)
    window_started_at: int = Field(index=True)
    attempts: int = 0
    version: int = 1
    updated_at: float = Field(default_factory=time.time)
    expires_at: float | None = Field(default=None, index=True)


__all__ = [
    "TurnObserverCredentialDB",
    "TurnObserverEnrollmentRateLimitDB",
    "TurnObserverIdentityDB",
    "TurnObserverIdentityMutationDB",
]
