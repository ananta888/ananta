from __future__ import annotations

import pytest

from agent.repositories.sfu_broadcast_repository import (
    InMemorySfuAudienceSnapshotRetentionRepository,
    InMemorySfuBroadcastAudienceRepository,
    InMemorySfuBroadcastRepositoryStore,
)
from agent.services.sfu_audience_snapshot_retention_service import (
    SfuAudienceSnapshotRetentionError,
    SfuAudienceSnapshotRetentionService,
)
from agent.services.sfu_broadcast_repository_ports import (
    SfuBroadcastAudience,
    SfuProjectionMutation,
)


def _audience() -> SfuBroadcastAudience:
    return SfuBroadcastAudience(
        id="audience-a", tenant_id="tenant-a", session_id="room-a",
        room_state_id="room-state-a", room_state_revision=1, status="active",
        ttl_seconds=10, retention_seconds=10, retention_status="live",
        expires_at=1_010.0, retain_until=1_020.0, tombstoned_at=None,
        tombstone_reason=None, fencing_token=1, version=1,
        audit_actor_ref="hub:test", audit_reason="test", request_digest="a" * 64,
        idempotency_key_digest="b" * 64, created_at=1_000.0, updated_at=1_000.0,
        audited_at=1_000.0, audience_ref="audience-ref", publication_ref="publication-a",
        audience_digest="c" * 64, policy_digest="d" * 64,
        membership_digest="e" * 64, policy_epoch=1, membership_epoch=1, key_epoch=1,
    )


def test_tombstone_and_purge_are_restart_safe_and_prevent_reactivation() -> None:
    now = [1_020.0]
    store = InMemorySfuBroadcastRepositoryStore()
    projection = InMemorySfuBroadcastAudienceRepository(store=store, clock=lambda: now[0])
    assert projection.save(SfuProjectionMutation(_audience(), 0, "create"), now=1_000.0).committed
    retention = SfuAudienceSnapshotRetentionService(
        InMemorySfuAudienceSnapshotRetentionRepository(store=store), clock=lambda: now[0]
    )
    tombstoned = retention.tombstone_snapshot(
        tenant_id="tenant-a", session_id="room-a", projection_id="audience-a",
        expected_version=1, retention_reason="last_route_ended", purge_grace_seconds=0,
        owner_id="hub-a", fencing_token=1, lease_expires_at=1_100.0,
    )
    assert tombstoned.committed and tombstoned.value.status == "tombstoned"
    page = retention.purge_once(
        owner_id="hub-a", fencing_token=1, lease_expires_at=1_100.0,
        page_size=10,
    )
    assert page.purged == 1
    assert projection.get(_audience().scope, "audience-a") is None
    replay = projection.save(SfuProjectionMutation(_audience(), 0, "stale-backup"), now=1_021.0)
    assert not replay.committed and replay.reason_code == "audience_snapshot_tombstoned"


def test_legal_hold_request_is_denied_instead_of_extending_sensitive_state() -> None:
    store = InMemorySfuBroadcastRepositoryStore()
    service = SfuAudienceSnapshotRetentionService(
        InMemorySfuAudienceSnapshotRetentionRepository(store=store), clock=lambda: 1_020.0
    )
    with pytest.raises(SfuAudienceSnapshotRetentionError, match="audience_retention_legal_hold_denied"):
        service.tombstone_snapshot(
            tenant_id="tenant-a", session_id="room-a", projection_id="audience-a",
            expected_version=1, retention_reason="legal_hold", purge_grace_seconds=0,
            owner_id="hub-a", fencing_token=1, lease_expires_at=1_100.0,
        )
