"""Lease-fenced audience snapshot tombstone and erasure lifecycle."""

from __future__ import annotations

import time
from typing import Callable

from agent.services.sfu_broadcast_repository_ports import (
    SfuAudienceRetentionFence,
    SfuAudienceRetentionPurgePage,
    SfuAudienceSnapshotRetentionRepositoryPort,
    SfuBroadcastRoomScope,
    SfuProjectionMutationResult,
    SfuBroadcastAudience,
)


MAX_PURGE_GRACE_SECONDS = 7 * 86_400
ALLOWED_RETENTION_REASONS = frozenset({
    "snapshot_expired",
    "last_route_ended",
    "consent_revoked",
    "membership_revoked",
    "tenant_deleted",
    "room_deleted",
})


class SfuAudienceSnapshotRetentionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SfuAudienceSnapshotRetentionService:
    """Coordinates lifecycle only; SQL and erasure mechanics remain in the port."""

    def __init__(
        self,
        repository: SfuAudienceSnapshotRetentionRepositoryPort,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def tombstone_snapshot(
        self,
        *,
        tenant_id: str,
        session_id: str,
        projection_id: str,
        expected_version: int,
        retention_reason: str,
        purge_grace_seconds: int,
        owner_id: str,
        fencing_token: int,
        lease_expires_at: float,
    ) -> SfuProjectionMutationResult[SfuBroadcastAudience]:
        if retention_reason == "legal_hold":
            raise SfuAudienceSnapshotRetentionError("audience_retention_legal_hold_denied")
        if retention_reason not in ALLOWED_RETENTION_REASONS:
            raise SfuAudienceSnapshotRetentionError("audience_retention_reason_invalid")
        if type(purge_grace_seconds) is not int or not 0 <= purge_grace_seconds <= MAX_PURGE_GRACE_SECONDS:
            raise SfuAudienceSnapshotRetentionError("audience_retention_grace_invalid")
        if type(expected_version) is not int or expected_version < 1:
            raise SfuAudienceSnapshotRetentionError("audience_retention_version_invalid")
        now = float(self._clock())
        return self._repository.tombstone(
            SfuBroadcastRoomScope(tenant_id, session_id),
            projection_id,
            expected_version=expected_version,
            retention_reason=retention_reason,
            purge_deadline=now + purge_grace_seconds,
            fence=SfuAudienceRetentionFence(owner_id, fencing_token, lease_expires_at),
            now=now,
        )

    def purge_once(
        self,
        *,
        owner_id: str,
        fencing_token: int,
        lease_expires_at: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuAudienceRetentionPurgePage:
        now = float(self._clock())
        return self._repository.purge_due(
            fence=SfuAudienceRetentionFence(owner_id, fencing_token, lease_expires_at),
            now=now,
            page_size=page_size,
            cursor=cursor,
        )

    def run(self, context) -> str | None:
        """Adapter for the Hub-owned background scheduler."""

        context.require_lease()
        page = self.purge_once(
            owner_id=context.lease.owner_id,
            fencing_token=context.lease.fencing_token,
            lease_expires_at=context.lease.lease_expires_at,
            page_size=context.batch_size_max,
            cursor=context.resume_cursor,
        )
        context.require_lease()
        return page.next_cursor


__all__ = [
    "ALLOWED_RETENTION_REASONS",
    "MAX_PURGE_GRACE_SECONDS",
    "SfuAudienceSnapshotRetentionError",
    "SfuAudienceSnapshotRetentionService",
]
