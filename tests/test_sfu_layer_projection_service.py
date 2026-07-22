from __future__ import annotations

import pytest

from agent.repositories.sfu_layer_projection_repository import InMemorySfuLayerProjectionRepository
from agent.services.sfu_layer_projection_service import (
    HmacSfuProjectionSigner,
    SfuLayerProjectionError,
    SfuLayerProjectionService,
    SfuProjectionMaterializeCommand,
    SfuProjectionScope,
)


NOW = 1_800_000_000.0


def _room(version: int, previous: int, token: int) -> dict:
    return {
        "schema": "ananta.sfu-room-session-projection.v1", "schema_version": 1,
        "tenant_ref": "tenant-a", "room_ref": "room-a", "projection_version": version,
        "session_projection_version": version, "membership_epoch": 4,
        "issued_at": "2027-01-15T08:00:00Z", "expires_at": "2027-01-15T08:00:30Z",
        "layer_control_mode": "manual_quality",
        "fencing": {"fencing_token": token, "expected_previous_projection_version": previous, "owner_generation": 1},
    }


def _service() -> SfuLayerProjectionService:
    return SfuLayerProjectionService(
        InMemorySfuLayerProjectionRepository(),
        HmacSfuProjectionSigner(
            b"x" * 32,
            key_id="test-v1",
            legacy_mode=True,
        ),
        clock=lambda: NOW,
    )


def test_materializes_cas_versioned_projection_and_conditional_cursor() -> None:
    service = _service()
    stored = service.materialize(SfuProjectionMaterializeCommand("tenant-a", "room-a", "room", "room-a", _room(1, 0, 1)))
    assert stored.signature_algorithm == "HMAC-SHA-256"
    assert stored.signature_algorithm_version == 1
    assert stored.signature_key_version == 1
    scope = SfuProjectionScope("tenant-a", "room-a", "user-a", 4)
    assert service.read(scope=scope, projection_kind="room", subject_ref="room-a", cursor=0) == stored
    assert service.read(scope=scope, projection_kind="room", subject_ref="room-a", cursor=1) is None


def test_rejects_gap_and_stale_fencing() -> None:
    service = _service()
    service.materialize(SfuProjectionMaterializeCommand("tenant-a", "room-a", "room", "room-a", _room(1, 0, 2)))
    with pytest.raises(SfuLayerProjectionError, match="sfu_projection_cas_conflict"):
        service.materialize(SfuProjectionMaterializeCommand("tenant-a", "room-a", "room", "room-a", _room(3, 2, 3)))
    with pytest.raises(SfuLayerProjectionError, match="sfu_projection_fencing_stale"):
        service.materialize(SfuProjectionMaterializeCommand("tenant-a", "room-a", "room", "room-a", _room(2, 1, 2)))
