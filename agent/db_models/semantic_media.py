from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class SemanticSessionMembershipDB(SQLModel, table=True):
    """Content-free membership authority for one semantic-compute epoch."""

    __tablename__ = "semantic_session_memberships"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "member_subject",
            "epoch",
            name="uq_semantic_membership_scope_epoch",
        ),
        sa.Index("ix_semantic_membership_scope", "tenant_id", "session_id", "epoch", "status"),
    )

    id: str = Field(default_factory=lambda: f"semantic-member-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    room_id: str | None = Field(default=None, index=True)
    member_subject: str = Field(index=True)
    role: str = Field(default="participant", index=True)
    epoch: int = Field(index=True)
    revision: int = 1
    status: str = Field(default="active", index=True)
    permissions: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    expires_at: float | None = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SemanticMediaCapabilityGrantDB(SQLModel, table=True):
    """Hub-issued, scope-bound semantic-media authority.

    A grant row is the durable source of truth.  Signed client copies are only
    references to this state and cannot survive a Hub-side revocation.
    """

    __tablename__ = "semantic_media_capability_grants"
    __table_args__ = (
        sa.Index(
            "ix_semantic_capability_grant_subject_scope",
            "tenant_id",
            "subject_id",
            "scope_kind",
            "scope_id",
            "epoch",
            "capability",
            "revoked_at",
            "expires_at",
        ),
        sa.Index(
            "ix_semantic_capability_grant_owner_scope",
            "tenant_id",
            "owner_id",
            "scope_kind",
            "scope_id",
            "epoch",
        ),
    )

    id: str = Field(primary_key=True)
    version: int = 1
    owner_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    subject_id: str = Field(index=True)
    subject_role: str = Field(index=True)
    capability: str = Field(index=True)
    scope_kind: str = Field(index=True)
    scope_id: str = Field(index=True)
    direction: str = Field(index=True)
    data_type: str
    purpose: str = Field(index=True)
    epoch: int = Field(index=True)
    issued_at: float = Field(index=True)
    expires_at: float = Field(index=True)
    issuer: str = "hub"
    signature: str = Field(repr=False)
    revoked_at: float | None = Field(default=None, index=True)
    revoked_by: str | None = Field(default=None, index=True)
    revocation_version: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SemanticComputeContractDB(SQLModel, table=True):
    """Hub-owned contract state; payload is bounded control data, never media."""

    __tablename__ = "semantic_compute_contracts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "epoch",
            "revision",
            name="uq_semantic_contract_scope_revision",
        ),
        sa.UniqueConstraint("active_scope_key", name="uq_semantic_contract_active_scope"),
        sa.Index("ix_semantic_contract_owner_scope", "tenant_id", "owner_subject", "session_id"),
        sa.Index("ix_semantic_contract_scope_status", "tenant_id", "session_id", "epoch", "status"),
    )

    id: str = Field(default_factory=lambda: f"semantic-contract-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    session_id: str = Field(index=True)
    room_id: str | None = Field(default=None, index=True)
    epoch: int = Field(index=True)
    revision: int
    digest: str = Field(index=True)
    status: str = Field(default="offered", index=True)
    profile: str = Field(default="off", index=True)
    security_mode: str = Field(default="strict_e2ee", index=True)
    consent_version: int = 0
    policy_version: str
    # Hub-owned, content-free protocol budget.  These counters deliberately
    # live next to the CAS revision so a restart or another Hub replica cannot
    # reset a bounded negotiation.
    negotiation_started_at_ms: int
    negotiation_round_count: int = 1
    negotiation_message_count: int = 1
    contract_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    active_scope_key: str | None = Field(default=None, index=True, repr=False)
    expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SemanticContractMutationDB(SQLModel, table=True):
    """Durable idempotency receipt for a tenant-scoped contract mutation."""

    __tablename__ = "semantic_contract_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "operation",
            "idempotency_key_digest",
            name="uq_semantic_mutation_idempotency",
        ),
    )

    id: str = Field(default_factory=lambda: f"semantic-mutation-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    operation: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    contract_id: str = Field(index=True)
    result_revision: int
    result_status: str
    result_digest: str = Field(repr=False)
    result_negotiation_started_at_ms: int
    result_negotiation_round_count: int
    result_negotiation_message_count: int
    result_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time)


class SemanticLeaseFenceDB(SQLModel, table=True):
    """Persistent monotonically increasing counter for one authority scope."""

    __tablename__ = "semantic_lease_fences"

    scope_key: str = Field(primary_key=True)
    last_token: int = 0
    updated_at: float = Field(default_factory=time.time)


class SemanticComputeLeaseDB(SQLModel, table=True):
    """Task-specific authority. A nullable unique key fences active overlap."""

    __tablename__ = "semantic_compute_leases"
    __table_args__ = (
        sa.UniqueConstraint("active_scope_key", name="uq_semantic_lease_active_scope"),
        sa.UniqueConstraint("scope_key", "fencing_token", name="uq_semantic_lease_fence"),
        sa.Index("ix_semantic_lease_scope_status", "tenant_id", "session_id", "epoch", "status"),
    )

    id: str = Field(default_factory=lambda: f"semantic-lease-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    contract_id: str = Field(index=True)
    contract_digest: str = Field(index=True)
    session_id: str = Field(index=True)
    epoch: int = Field(index=True)
    task_type: str = Field(index=True)
    audience: str = Field(index=True)
    role: str = Field(index=True)
    executor_id: str = Field(index=True)
    sequence_start: int
    sequence_end: int
    fencing_token: int = Field(index=True)
    resource_budget: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    scope_key: str = Field(index=True, repr=False)
    active_scope_key: str | None = Field(default=None, index=True, repr=False)
    status: str = Field(default="active", index=True)
    issued_at: float = Field(default_factory=time.time)
    expires_at: float = Field(index=True)
    deadline_at: float = Field(index=True)
    version: int = 1
    revoked_at: float | None = None
    updated_at: float = Field(default_factory=time.time)


class SemanticComputeCandidateKeyDB(SQLModel, table=True):
    """Authenticated browser signing key bound to one Hub membership epoch."""

    __tablename__ = "semantic_compute_candidate_keys"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "epoch",
            "member_subject",
            "key_id",
            name="uq_semantic_candidate_key_scope",
        ),
        sa.Index(
            "ix_semantic_candidate_key_active",
            "tenant_id",
            "session_id",
            "epoch",
            "member_subject",
            "status",
            "expires_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"semantic-candidate-key-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    epoch: int = Field(index=True)
    member_subject: str = Field(index=True)
    key_id: str = Field(index=True)
    public_key_b64: str = Field(repr=False)
    status: str = Field(default="active", index=True)
    expires_at: float = Field(index=True)
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SemanticCapabilityAdvertisementDB(SQLModel, table=True):
    """Verified, short-lived candidate input; never grants execution authority."""

    __tablename__ = "semantic_capability_advertisements"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "advertisement_id", name="uq_semantic_capability_advertisement"),
        sa.Index(
            "ix_semantic_capability_current",
            "tenant_id",
            "session_id",
            "epoch",
            "sender_subject",
            "status",
            "expires_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"semantic-capability-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    room_id: str | None = Field(default=None, index=True)
    epoch: int = Field(index=True)
    sender_subject: str = Field(index=True)
    advertisement_id: str = Field(index=True)
    key_id: str = Field(index=True)
    payload_digest: str = Field(index=True, repr=False)
    normalized_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    # Hub-side conservative reductions. Browser values cannot raise these.
    observed_capacity: int = 0
    user_limit: int = 0
    reserve_capacity: int = 0
    recent_error_rate: float = 1.0
    reputation: int = 0
    active_assignments: int = 0
    failure_domain: str = Field(repr=False)
    status: str = Field(default="active", index=True)
    expires_at: float = Field(index=True)
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SemanticComputeScheduleReceiptDB(SQLModel, table=True):
    """Durable idempotency receipt for a Hub scheduling decision."""

    __tablename__ = "semantic_compute_schedule_receipts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "contract_id",
            "idempotency_key_digest",
            name="uq_semantic_schedule_idempotency",
        ),
        sa.Index("ix_semantic_schedule_receipt_expiry", "expires_at", "created_at"),
    )

    id: str = Field(default_factory=lambda: f"semantic-schedule-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    contract_id: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    result_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)


class SemanticComputeLeaseMutationDB(SQLModel, table=True):
    """Atomic idempotency receipt for a scoped lease CAS mutation."""

    __tablename__ = "semantic_compute_lease_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "lease_id",
            "operation",
            "idempotency_key_digest",
            name="uq_semantic_lease_mutation_idempotency",
        ),
    )

    id: str = Field(default_factory=lambda: f"semantic-lease-mutation-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    lease_id: str = Field(index=True)
    operation: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    result_version: int
    created_at: float = Field(default_factory=time.time)


class SemanticMediaAuditEventDB(SQLModel, table=True):
    """Content-free, retention-bound audit record for Hub decisions.

    Only keyed digests and authority-reference digests are persisted.  Media,
    transcript, feature and identity payloads have no column in this model.
    """

    __tablename__ = "semantic_media_audit_events"
    __table_args__ = (
        sa.UniqueConstraint(
            "idempotency_digest",
            name="uq_semantic_media_audit_idempotency",
        ),
        sa.Index(
            "ix_semantic_media_audit_scope_page",
            "tenant_digest",
            "scope_digest",
            "created_at_ms",
            "id",
        ),
        sa.Index(
            "ix_semantic_media_audit_expiry",
            "expires_at_ms",
            "id",
        ),
    )

    id: str = Field(primary_key=True)
    idempotency_digest: str = Field(index=True, repr=False)
    tenant_digest: str = Field(index=True, repr=False)
    scope_digest: str = Field(index=True, repr=False)
    event_type: str = Field(index=True)
    transition: str
    reason_code: str
    epoch: int = Field(index=True)
    contract_ref: str | None = Field(default=None, index=True, repr=False)
    lease_ref: str | None = Field(default=None, index=True, repr=False)
    job_ref: str | None = Field(default=None, index=True, repr=False)
    created_at_ms: int = Field(index=True)
    expires_at_ms: int = Field(index=True)


class SemanticMediaAuditOutboxDB(SQLModel, table=True):
    """Pending content-free audit event committed with its domain mutation.

    The row intentionally mirrors only the final audit schema. It has no JSON
    payload column, so media, transcripts, identities and credentials cannot
    be smuggled through a generic outbox envelope.
    """

    __tablename__ = "semantic_media_audit_outbox"
    __table_args__ = (
        sa.UniqueConstraint(
            "idempotency_digest",
            name="uq_semantic_media_audit_outbox_idempotency",
        ),
        sa.Index(
            "ix_semantic_media_audit_outbox_dispatch",
            "available_at_ms",
            "created_at_ms",
            "id",
        ),
        sa.Index(
            "ix_semantic_media_audit_outbox_scope",
            "tenant_digest",
            "scope_digest",
            "expires_at_ms",
        ),
    )

    id: str = Field(primary_key=True)
    event_id: str = Field(index=True, repr=False)
    idempotency_digest: str = Field(index=True, repr=False)
    tenant_digest: str = Field(index=True, repr=False)
    scope_digest: str = Field(index=True, repr=False)
    event_type: str = Field(index=True)
    transition: str
    reason_code: str
    epoch: int = Field(index=True)
    contract_ref: str | None = Field(default=None, index=True, repr=False)
    lease_ref: str | None = Field(default=None, index=True, repr=False)
    job_ref: str | None = Field(default=None, index=True, repr=False)
    created_at_ms: int = Field(index=True)
    expires_at_ms: int = Field(index=True)
    available_at_ms: int = Field(index=True)


class SemanticSfuRoomStateDB(SQLModel, table=True):
    """Durable, content-free Hub projection for one admitted SFU room."""

    __tablename__ = "semantic_sfu_room_states"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "session_id", name="uq_semantic_sfu_room_scope"),
        sa.Index("ix_semantic_sfu_room_scope_revision", "tenant_id", "session_id", "revision"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    revision: int = Field(default=0, index=True)
    participants: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    publications: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    subscriptions: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SemanticSfuAdmissionReceiptDB(SQLModel, table=True):
    """Short-lived durable idempotency receipt for an SFU Hub mutation."""

    __tablename__ = "semantic_sfu_admission_receipts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "actor_id",
            "operation",
            "idempotency_key_digest",
            name="uq_semantic_sfu_receipt_idempotency",
        ),
        sa.Index("ix_semantic_sfu_receipt_expiry", "expires_at", "created_at"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    actor_id: str = Field(index=True)
    operation: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    result_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)


__all__ = [
    "SemanticMediaCapabilityGrantDB",
    "SemanticComputeContractDB",
    "SemanticComputeCandidateKeyDB",
    "SemanticComputeLeaseDB",
    "SemanticComputeLeaseMutationDB",
    "SemanticComputeScheduleReceiptDB",
    "SemanticCapabilityAdvertisementDB",
    "SemanticContractMutationDB",
    "SemanticLeaseFenceDB",
    "SemanticMediaAuditEventDB",
    "SemanticMediaAuditOutboxDB",
    "SemanticSfuAdmissionReceiptDB",
    "SemanticSfuRoomStateDB",
    "SemanticSessionMembershipDB",
]
