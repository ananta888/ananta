"""Focused persistence ports for Hub-owned SFU broadcast projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable


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


@runtime_checkable
class SfuBroadcastAudienceRepositoryPort(Protocol):
    def get(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
    ) -> SfuBroadcastAudience | None: ...

    def save(
        self,
        mutation: SfuProjectionMutation[SfuBroadcastAudience],
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuBroadcastAudience]: ...

    def expire(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
        *,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuBroadcastAudience]: ...

    def page(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuBroadcastAudience]: ...

    def page_expired(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuBroadcastAudience]: ...

    def page_reconciliation(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        current_room_state_revision: int,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuBroadcastAudience]: ...

    def page_retention_due(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuBroadcastAudience]: ...


@runtime_checkable
class SfuReceiverGroupRepositoryPort(Protocol):
    def get(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
    ) -> SfuReceiverGroup | None: ...

    def save(
        self,
        mutation: SfuProjectionMutation[SfuReceiverGroup],
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuReceiverGroup]: ...

    def expire(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
        *,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuReceiverGroup]: ...

    def page(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuReceiverGroup]: ...

    def page_expired(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuReceiverGroup]: ...

    def page_reconciliation(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        current_room_state_revision: int,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuReceiverGroup]: ...


@runtime_checkable
class SfuFanoutRouteRepositoryPort(Protocol):
    def get(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
    ) -> SfuFanoutRoute | None: ...

    def save(
        self,
        mutation: SfuProjectionMutation[SfuFanoutRoute],
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuFanoutRoute]: ...

    def expire(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
        *,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuFanoutRoute]: ...

    def page(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuFanoutRoute]: ...

    def page_expired(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuFanoutRoute]: ...

    def page_reconciliation(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        current_room_state_revision: int,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuFanoutRoute]: ...


__all__ = [
    "SfuBroadcastAudience",
    "SfuBroadcastAudienceRepositoryPort",
    "SfuBroadcastRoomScope",
    "SfuFanoutRoute",
    "SfuFanoutRouteRepositoryPort",
    "SfuMutationStatus",
    "SfuProjectionEnvelope",
    "SfuProjectionMutation",
    "SfuProjectionMutationResult",
    "SfuProjectionPage",
    "SfuReceiverGroup",
    "SfuReceiverGroupRepositoryPort",
]
