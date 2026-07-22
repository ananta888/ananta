from __future__ import annotations

import pytest

from agent.repositories.sfu_vendor_identity_repository import (
    InMemorySfuVendorIdentityRepository,
    InMemorySfuVendorIdentityStore,
)
from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope
from agent.services.sfu_vendor_identity_service import (
    SfuVendorIdentityError,
    SfuVendorIdentityService,
)


def _service(now: list[float], store=None) -> SfuVendorIdentityService:
    envelope = derive_sfu_hub_envelope("test-master-secret-with-at-least-32-bytes", key_id="test-v1")
    return SfuVendorIdentityService(
        InMemorySfuVendorIdentityRepository(store=store), envelope, clock=lambda: now[0]
    )


def test_identity_is_stable_opaque_and_restart_resolvable_only_inside_hub_service() -> None:
    now = [1_000.0]
    store = InMemorySfuVendorIdentityStore()
    service = _service(now, store)
    first = service.issue_identity(
        tenant_id="tenant-a", room_id="room-a", membership_ref="canonical-user-alice",
        membership_epoch=7, identity_epoch=7, ttl_seconds=600, fencing_token=1,
    )
    reconnect = service.issue_identity(
        tenant_id="tenant-a", room_id="room-a", membership_ref="canonical-user-alice",
        membership_epoch=7, identity_epoch=7, ttl_seconds=600, fencing_token=2,
    )
    assert first.identity_handle == reconnect.identity_handle
    assert "alice" not in first.identity_handle
    restarted = _service(now, store)
    assert restarted.resolve_identity(
        tenant_id="tenant-a", room_id="room-a", identity_handle=first.identity_handle,
        membership_epoch=7, identity_epoch=7,
    ) == "canonical-user-alice"
    with pytest.raises(SfuVendorIdentityError, match="sfu_vendor_identity_stale"):
        restarted.resolve_identity(
            tenant_id="tenant-a", room_id="room-b", identity_handle=first.identity_handle,
            membership_epoch=7, identity_epoch=7,
        )


def test_destination_is_bound_to_route_publication_audience_and_key_epoch() -> None:
    now = [1_000.0]
    service = _service(now)
    identity = service.issue_identity(
        tenant_id="tenant-a", room_id="room-a", membership_ref="alice",
        membership_epoch=7, identity_epoch=7, ttl_seconds=600, fencing_token=1,
    )
    destination = service.authorize_destination(
        tenant_id="tenant-a", room_id="room-a", identity_handle=identity.identity_handle,
        membership_epoch=7, identity_epoch=7, route_ref="route-a",
        publication_ref="publication-a", audience_ref="audience-a",
        route_epoch=3, key_epoch=4, fencing_token=5,
    )
    audience = service.authorized_data_audience(
        tenant_id="tenant-a", room_id="room-a", publication_ref="publication-a",
        audience_ref="audience-a", route_ref="route-a",
        destination_handles=(destination.destination_handle,), membership_epoch=7,
        route_epoch=3, key_epoch=4, fencing_token=5, expires_at_ms=1_030_000,
    )
    assert audience.destination_handles == (identity.identity_handle,)
    with pytest.raises(SfuVendorIdentityError, match="sfu_destination_authorization_stale"):
        service.resolve_destination(
            tenant_id="tenant-a", room_id="room-a",
            destination_handle=destination.destination_handle,
            route_ref="route-b", publication_ref="publication-a", audience_ref="audience-a",
            membership_epoch=7, route_epoch=3, key_epoch=4, fencing_token=5,
        )
    assert service.revoke_membership(
        tenant_id="tenant-a", room_id="room-a", membership_ref="alice", fencing_token=6
    ) == 1
    with pytest.raises(SfuVendorIdentityError, match="sfu_vendor_identity_stale"):
        service.resolve_identity(
            tenant_id="tenant-a", room_id="room-a", identity_handle=identity.identity_handle,
            membership_epoch=7, identity_epoch=7,
        )
