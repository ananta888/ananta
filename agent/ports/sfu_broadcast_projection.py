"""Focused persistence ports for Hub-owned SFU broadcast projections."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.models.sfu_broadcast_projection import (
    SfuAtomicGroupProjectionMutation,
    SfuAudienceRetentionFence,
    SfuAudienceRetentionPurgePage,
    SfuBroadcastAudience,
    SfuBroadcastRoomScope,
    SfuFanoutRoute,
    SfuProjectionMutation,
    SfuProjectionMutationResult,
    SfuProjectionPage,
    SfuReceiverGroup,
)


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


@runtime_checkable
class SfuAudienceSnapshotRetentionRepositoryPort(Protocol):
    def tombstone(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
        *,
        expected_version: int,
        retention_reason: str,
        purge_deadline: float,
        fence: SfuAudienceRetentionFence,
        now: float,
    ) -> SfuProjectionMutationResult[SfuBroadcastAudience]: ...

    def purge_due(
        self,
        *,
        fence: SfuAudienceRetentionFence,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuAudienceRetentionPurgePage: ...

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
class SfuAtomicGroupProjectionRepositoryPort(Protocol):
    def save_authorized(
        self,
        mutation: SfuAtomicGroupProjectionMutation,
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuReceiverGroup]: ...


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
    "SfuAtomicGroupProjectionRepositoryPort",
    "SfuAudienceSnapshotRetentionRepositoryPort",
    "SfuBroadcastAudienceRepositoryPort",
    "SfuFanoutRouteRepositoryPort",
    "SfuReceiverGroupRepositoryPort",
]
