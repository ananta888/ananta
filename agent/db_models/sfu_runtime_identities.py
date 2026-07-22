from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class SfuRuntimeIdentityDB(SQLModel, table=True):
    """Hub-owned identity and optimistic fence for one SFU runtime principal."""

    __tablename__ = "sfu_runtime_identities"
    __table_args__ = (
        sa.UniqueConstraint("node_id", name="uq_sfu_runtime_identity_node"),
        sa.Index("ix_sfu_runtime_identity_status_version", "status", "version"),
    )

    id: str = Field(default_factory=lambda: f"sfu-runtime-{uuid.uuid4().hex}", primary_key=True)
    node_id: str = Field(index=True)
    runtime_control_mode: str = Field(index=True)
    roles: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="active", index=True)
    version: int = Field(default=1, index=True)
    active_credential_id: str
    previous_credential_id: str | None = Field(default=None, index=True)
    actor: str = Field(index=True)
    reason: str
    enrolled_at: float = Field(default_factory=time.time)
    rotated_at: float | None = None
    revoked_at: float | None = Field(default=None, index=True)
    revocation_deadline_at: float | None = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SfuRuntimeCredentialDB(SQLModel, table=True):
    """Public metadata only; private keys and API secrets have no schema field."""

    __tablename__ = "sfu_runtime_credentials"
    __table_args__ = (
        sa.UniqueConstraint("credential_fingerprint", name="uq_sfu_runtime_credential_fingerprint"),
        sa.UniqueConstraint("public_key_fingerprint", name="uq_sfu_runtime_public_key_fingerprint"),
        sa.UniqueConstraint("proof_nonce_digest", name="uq_sfu_runtime_proof_nonce"),
        sa.Index("ix_sfu_runtime_credential_identity_status", "identity_id", "status"),
    )

    id: str = Field(default_factory=lambda: f"sfu-credential-{uuid.uuid4().hex}", primary_key=True)
    identity_id: str = Field(foreign_key="sfu_runtime_identities.id", index=True)
    credential_kind: str = Field(index=True)
    public_key_fingerprint: str = Field(index=True)
    credential_fingerprint: str = Field(index=True)
    proof_nonce_digest: str = Field(index=True, repr=False)
    certificate_serial: str | None = Field(default=None, index=True)
    certificate_sans: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    certificate_ekus: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    certificate_not_before: float | None = None
    certificate_not_after: float | None = None
    status: str = Field(default="active", index=True)
    valid_from: float = Field(default_factory=time.time)
    overlap_until: float | None = Field(default=None, index=True)
    revoked_at: float | None = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)


class SfuRuntimeIdentityMutationDB(SQLModel, table=True):
    """Immutable idempotency receipt and content-free security audit record."""

    __tablename__ = "sfu_runtime_identity_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "actor", "idempotency_key_digest", name="uq_sfu_runtime_mutation_actor_idempotency"
        ),
        sa.Index("ix_sfu_runtime_mutation_node_version", "node_id", "result_version"),
    )

    id: str = Field(default_factory=lambda: f"sfu-mutation-{uuid.uuid4().hex}", primary_key=True)
    identity_id: str = Field(index=True)
    node_id: str = Field(index=True)
    operation: str = Field(index=True)
    expected_version: int
    result_version: int = Field(index=True)
    result_status: str
    actor: str = Field(index=True)
    reason: str
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    response_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    audited_at: float = Field(default_factory=time.time, index=True)


class SfuRuntimeEnrollmentRateLimitDB(SQLModel, table=True):
    """Durable per-actor/source enrollment bucket shared by every Hub instance."""

    __tablename__ = "sfu_runtime_enrollment_rate_limits"
    __table_args__ = (
        sa.UniqueConstraint(
            "actor", "source_digest", "window_started_at", name="uq_sfu_runtime_enrollment_bucket"
        ),
    )

    id: str = Field(primary_key=True)
    actor: str = Field(index=True)
    source_digest: str = Field(index=True, repr=False)
    window_started_at: int = Field(index=True)
    attempts: int = 0
    version: int = 1
    updated_at: float = Field(default_factory=time.time)


__all__ = [
    "SfuRuntimeCredentialDB",
    "SfuRuntimeEnrollmentRateLimitDB",
    "SfuRuntimeIdentityDB",
    "SfuRuntimeIdentityMutationDB",
]
