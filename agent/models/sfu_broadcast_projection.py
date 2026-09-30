"""Value types of the Hub-owned SFU broadcast projections.

The persistence protocols over these projections live in
:mod:`agent.ports.sfu_broadcast_projection`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar


SfuProjectionStatus = Literal[
    "pending",
    "active",
    "draining",
    "expired",
    "revoked",
    "tombstoned",
]


SfuRetentionStatus = Literal["live", "retained", "purge_pending", "purged"]


SfuMutationStatus = Literal[
    "saved",
    "conflict",
    "stale_epoch",
    "expired",
    "not_found",
]


@dataclass(frozen=True, slots=True)
class SfuBroadcastRoomScope:
    tenant_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class SfuProjectionEnvelope:
    id: str
    tenant_id: str
    session_id: str
    room_state_id: str
    room_state_revision: int
    status: SfuProjectionStatus
    ttl_seconds: int
    retention_seconds: int
    retention_status: SfuRetentionStatus
    expires_at: float
    retain_until: float
    tombstoned_at: float | None
    tombstone_reason: str | None
    fencing_token: int
    version: int
    audit_actor_ref: str
    audit_reason: str
    request_digest: str
    idempotency_key_digest: str
    created_at: float
    updated_at: float
    audited_at: float

    @property
    def scope(self) -> SfuBroadcastRoomScope:
        return SfuBroadcastRoomScope(self.tenant_id, self.session_id)


@dataclass(frozen=True, slots=True)
class SfuBroadcastAudience(SfuProjectionEnvelope):
    audience_ref: str
    publication_ref: str
    audience_digest: str
    policy_digest: str
    membership_digest: str
    policy_epoch: int
    membership_epoch: int
    key_epoch: int


@dataclass(frozen=True, slots=True)
class SfuReceiverGroup(SfuProjectionEnvelope):
    receiver_group_ref: str
    subscription_ref: str
    group_digest: str
    membership_digest: str
    key_digest: str
    membership_epoch: int
    key_epoch: int
    topology_epoch: int


@dataclass(frozen=True, slots=True)
class SfuFanoutRoute(SfuProjectionEnvelope):
    route_ref: str
    audience_projection_id: str
    receiver_group_projection_id: str
    publication_ref: str
    subscription_ref: str
    route_digest: str
    policy_digest: str
    membership_digest: str
    key_digest: str
    policy_epoch: int
    membership_epoch: int
    key_epoch: int
    route_epoch: int
    topology_epoch: int


ProjectionT = TypeVar("ProjectionT", bound=SfuProjectionEnvelope)


@dataclass(frozen=True, slots=True)
class SfuProjectionMutation(Generic[ProjectionT]):
    value: ProjectionT
    expected_version: int | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class SfuAtomicGroupProjectionMutation:
    """Group write whose Audience epochs must be checked atomically."""

    audience_projection_id: str
    mutation: SfuProjectionMutation[SfuReceiverGroup]
    expected_policy_epoch: int
    expected_membership_epoch: int
    expected_key_epoch: int


@dataclass(frozen=True, slots=True)
class SfuProjectionMutationResult(Generic[ProjectionT]):
    status: SfuMutationStatus
    value: ProjectionT | None = None
    replayed: bool = False
    reason_code: str | None = None

    @property
    def committed(self) -> bool:
        return self.status == "saved"


@dataclass(frozen=True, slots=True)
class SfuProjectionPage(Generic[ProjectionT]):
    items: tuple[ProjectionT, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SfuAudienceRetentionFence:
    owner_id: str
    fencing_token: int
    lease_expires_at: float


@dataclass(frozen=True, slots=True)
class SfuAudienceRetentionPurgePage:
    purged: int
    next_cursor: str | None


__all__ = [
    "ProjectionT",
    "SfuAtomicGroupProjectionMutation",
    "SfuAudienceRetentionFence",
    "SfuAudienceRetentionPurgePage",
    "SfuBroadcastAudience",
    "SfuBroadcastRoomScope",
    "SfuFanoutRoute",
    "SfuMutationStatus",
    "SfuProjectionEnvelope",
    "SfuProjectionMutation",
    "SfuProjectionMutationResult",
    "SfuProjectionPage",
    "SfuProjectionStatus",
    "SfuReceiverGroup",
    "SfuRetentionStatus",
]
