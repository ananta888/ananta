"""Narrow persistence and secret-envelope ports for SFU group keys."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.models.sfu_group_keys import (
    SfuGroupKeyDeliveryPage,
    SfuGroupKeyEpochState,
    SfuGroupKeyMutationResult,
    SfuGroupKeyPackageWrite,
    SfuGroupKeyReceipt,
    SfuHubBlindIndex,
    SfuHubSealedSecret,
)


@runtime_checkable
class SfuBroadcastGroupKeyRepositoryPort(Protocol):
    def receipt(
        self,
        *,
        tenant_id: str,
        actor_digest: str,
        operation: str,
        idempotency_key_digest: str,
        now_ms: int,
    ) -> SfuGroupKeyReceipt | None: ...

    def latest(self, *, tenant_id: str, session_id: str, room_id: str) -> SfuGroupKeyEpochState | None: ...

    def get(self, *, tenant_id: str, authorization_id: str) -> SfuGroupKeyEpochState | None: ...

    def create_epoch(
        self,
        state: SfuGroupKeyEpochState,
        receipt: SfuGroupKeyReceipt,
        *,
        now_ms: int,
    ) -> SfuGroupKeyMutationResult: ...

    def deliver(
        self,
        *,
        tenant_id: str,
        authorization_id: str,
        expected_version: int,
        expected_fencing_token: int,
        packages: tuple[SfuGroupKeyPackageWrite, ...],
        receipt: SfuGroupKeyReceipt,
        now_ms: int,
    ) -> SfuGroupKeyMutationResult: ...

    def read_for_recipient(
        self,
        *,
        tenant_id: str,
        session_id: str,
        recipient_digest: str,
        membership_epoch: int,
        cursor: str,
        limit: int,
        now_ms: int,
    ) -> SfuGroupKeyDeliveryPage: ...

    def acknowledge(
        self,
        *,
        tenant_id: str,
        authorization_id: str,
        package_ref: str,
        recipient_digest: str,
        membership_epoch: int,
        now_ms: int,
    ) -> SfuGroupKeyMutationResult: ...

    def purge_expired(self, *, now_ms: int, limit: int) -> int: ...

    def rotate_envelopes(self, *, limit: int) -> int: ...


@runtime_checkable
class SfuHubSecretEnvelopePort(Protocol):
    @property
    def active_key_id(self) -> str: ...

    @property
    def active_blind_key_id(self) -> str: ...

    def blind(self, *, purpose: str, scope: str, value: str) -> str: ...

    def blind_index(self, *, purpose: str, scope: str, value: str) -> SfuHubBlindIndex: ...

    def blind_candidates(self, *, purpose: str, scope: str, value: str) -> tuple[SfuHubBlindIndex, ...]: ...

    def seal(self, plaintext: bytes, *, purpose: str, scope: str, aad: bytes) -> SfuHubSealedSecret: ...

    def open(self, envelope: SfuHubSealedSecret, *, purpose: str, scope: str, aad: bytes) -> bytes: ...

    def rewrap(
        self,
        envelope: SfuHubSealedSecret,
        *,
        purpose: str,
        scope: str,
        aad: bytes,
    ) -> SfuHubSealedSecret: ...


__all__ = [
    "SfuBroadcastGroupKeyRepositoryPort",
    "SfuHubSecretEnvelopePort",
]
