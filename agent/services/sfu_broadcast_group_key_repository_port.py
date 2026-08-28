"""Persistent Hub-side state contract for bounded SFU group-key delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from agent.services.webrtc_group_key_authorization_service import GroupKeyEpochAuthorization


@dataclass(frozen=True, slots=True)
class SfuGroupKeyEpochState:
    authorization: GroupKeyEpochAuthorization
    session_id: str
    publisher_digest: str
    distribution_mode: Literal["bounded_rewrap"] = "bounded_rewrap"
    status: Literal["active", "revoked", "expired", "tombstoned"] = "active"
    package_count: int = 0
    total_package_bytes: int = 0
    delivered_member_ids: tuple[str, ...] = ()
    acknowledged_member_ids: tuple[str, ...] = ()
    fencing_token: int = 1
    version: int = 1


@dataclass(frozen=True, slots=True)
class SfuGroupKeyPackageWrite:
    recipient_id: str
    recipient_digest: str
    package_ref: str
    opaque_package: bytes
    package_digest: str
    expires_at_ms: int

    def __repr__(self) -> str:
        return (
            "SfuGroupKeyPackageWrite("
            f"recipient_digest={self.recipient_digest!r}, package_ref={self.package_ref!r}, "
            "opaque_package=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class SfuGroupKeyPackageDelivery:
    authorization: GroupKeyEpochAuthorization
    publisher_id: str
    package_ref: str
    opaque_package: bytes
    package_digest: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class SfuGroupKeyReceipt:
    tenant_id: str
    actor_digest: str
    operation: Literal["prepare", "deliver"]
    idempotency_key_digest: str
    request_digest: str
    result: dict
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class SfuGroupKeyMutationResult:
    status: Literal["saved", "conflict", "stale_epoch", "expired", "not_found"]
    state: SfuGroupKeyEpochState | None = None
    replayed: bool = False
    reason_code: str | None = None

    @property
    def committed(self) -> bool:
        return self.status == "saved"


@dataclass(frozen=True, slots=True)
class SfuGroupKeyDeliveryPage:
    items: tuple[SfuGroupKeyPackageDelivery, ...]
    next_cursor: str


@runtime_checkable
class SfuBroadcastGroupKeyRepositoryPort(Protocol):
    def receipt(
        self, *, tenant_id: str, actor_digest: str, operation: str,
        idempotency_key_digest: str, now_ms: int,
    ) -> SfuGroupKeyReceipt | None: ...

    def latest(self, *, tenant_id: str, session_id: str, room_id: str) -> SfuGroupKeyEpochState | None: ...

    def get(self, *, tenant_id: str, authorization_id: str) -> SfuGroupKeyEpochState | None: ...

    def create_epoch(
        self, state: SfuGroupKeyEpochState, receipt: SfuGroupKeyReceipt,
        *, now_ms: int,
    ) -> SfuGroupKeyMutationResult: ...

    def deliver(
        self, *, tenant_id: str, authorization_id: str, expected_version: int,
        expected_fencing_token: int, packages: tuple[SfuGroupKeyPackageWrite, ...],
        receipt: SfuGroupKeyReceipt, now_ms: int,
    ) -> SfuGroupKeyMutationResult: ...

    def read_for_recipient(
        self, *, tenant_id: str, session_id: str, recipient_digest: str,
        membership_epoch: int, cursor: str, limit: int, now_ms: int,
    ) -> SfuGroupKeyDeliveryPage: ...

    def acknowledge(
        self, *, tenant_id: str, authorization_id: str, package_ref: str,
        recipient_digest: str, membership_epoch: int, now_ms: int,
    ) -> SfuGroupKeyMutationResult: ...

    def purge_expired(self, *, now_ms: int, limit: int) -> int: ...

    def rotate_envelopes(self, *, limit: int) -> int: ...


__all__ = [
    "SfuBroadcastGroupKeyRepositoryPort",
    "SfuGroupKeyDeliveryPage",
    "SfuGroupKeyEpochState",
    "SfuGroupKeyMutationResult",
    "SfuGroupKeyPackageDelivery",
    "SfuGroupKeyPackageWrite",
    "SfuGroupKeyReceipt",
]
