from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlmodel import SQLModel

from agent.database import engine
from agent.repositories.webrtc_epoch_repository import WebrtcEpochRepository
from agent.services.webrtc_epoch_service import WebrtcEpochService
from agent.services.webrtc_group_key_authorization_service import (
    GroupKeyAuthorizationError,
    WebrtcGroupKeyAuthorizationService,
    member_set_digest,
)


def test_membership_change_rotates_signed_opaque_group_epoch() -> None:
    SQLModel.metadata.create_all(engine)
    room = f"room-{uuid.uuid4()}"
    epochs = WebrtcEpochService(WebrtcEpochRepository(), clock=lambda: 1000)
    assert epochs.claim_epoch(scope_kind="room", scope_id=room, hub_id="hub").epoch == 1
    service = WebrtcGroupKeyAuthorizationService(
        private_key=Ed25519PrivateKey.generate(),
        hub_key_id="hub-key",
        epoch_repository=WebrtcEpochRepository(),
        clock=lambda: 1000,
    )
    first = service.authorize(
        tenant_id="tenant",
        room_id=room,
        publication_id="pub",
        epoch=1,
        previous_epoch=0,
        active_member_ids=["alice"],
        key_package_refs={"alice": "pkg-a"},
        reason="create",
        rekey_deadline_ms=1_005_000,
        expires_at_ms=1_100_000,
    )
    assert first.member_set_digest == member_set_digest(["alice"])
    # The signed object carries references only, never a content key.
    assert "content_key" not in first.__dict__
    assert epochs.claim_epoch(scope_kind="room", scope_id=room, hub_id="hub", advance=True).epoch == 2
    joined = service.authorize(
        tenant_id="tenant",
        room_id=room,
        publication_id="pub",
        epoch=2,
        previous_epoch=1,
        active_member_ids=["bob", "alice"],
        key_package_refs={"alice": "pkg-a2", "bob": "pkg-b2"},
        reason="join",
        rekey_deadline_ms=1_005_000,
        expires_at_ms=1_100_000,
    )
    assert joined.member_ids == ("alice", "bob")
    with pytest.raises(GroupKeyAuthorizationError, match="epoch_not_authoritative"):
        service.authorize(
            tenant_id="tenant",
            room_id=room,
            publication_id="pub",
            epoch=1,
            previous_epoch=0,
            active_member_ids=["alice"],
            key_package_refs={"alice": "old"},
            reason="refresh",
            rekey_deadline_ms=1_005_000,
            expires_at_ms=1_100_000,
        )


def test_missing_member_package_is_denied() -> None:
    SQLModel.metadata.create_all(engine)
    room = f"room-{uuid.uuid4()}"
    epochs = WebrtcEpochService(WebrtcEpochRepository(), clock=lambda: 1000)
    epochs.claim_epoch(scope_kind="room", scope_id=room, hub_id="hub")
    service = WebrtcGroupKeyAuthorizationService(
        private_key=Ed25519PrivateKey.generate(),
        hub_key_id="hub",
        epoch_repository=WebrtcEpochRepository(),
        clock=lambda: 1000,
    )
    with pytest.raises(GroupKeyAuthorizationError, match="key_package_set_mismatch"):
        service.authorize(
            tenant_id="t",
            room_id=room,
            publication_id="p",
            epoch=1,
            previous_epoch=0,
            active_member_ids=["alice", "bob"],
            key_package_refs={"alice": "pkg"},
            reason="create",
            rekey_deadline_ms=1_001_000,
            expires_at_ms=1_100_000,
        )
