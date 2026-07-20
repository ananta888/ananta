from __future__ import annotations

import base64
from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.webrtc_peer_key_repository import WebrtcPeerKeyRepository
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from agent.services.webrtc_peer_identity_service import (
    PeerIdentityError,
    PeerMembership,
    WebrtcPeerIdentityService,
    derive_hub_identity_key,
    spki_fingerprint,
)


def _device_key() -> tuple[str, str]:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    encoded = base64.b64encode(public).decode()
    return encoded, spki_fingerprint(encoded)


def test_signed_package_binds_membership_device_recipient_epoch_and_contract() -> None:
    public_key, fingerprint = _device_key()
    membership = PeerMembership("member-1", "tenant-1", "session", "sess-1", "alice", "dev-1", 3, True)
    memberships = {membership.membership_id: membership}
    service = WebrtcPeerIdentityService(
        derive_hub_identity_key(b"h" * 32),
        hub_key_id="hub-key-1",
        membership_lookup=memberships.get,
        device_fingerprint_lookup=lambda peer, device: fingerprint if (peer, device) == ("alice", "dev-1") else None,
        clock=lambda: 1000,
    )
    package = service.issue_key_package(
        membership_id="member-1",
        recipient_peer_id="bob",
        epoch=8,
        ecdh_public_key_spki_b64=public_key,
        security_contract_digest="a" * 64,
        expires_at_ms=1_100_000,
    )
    assert service.verify_key_package(
        package, expected_recipient_peer_id="bob", expected_scope_id="sess-1", expected_epoch=8
    ) == (True, "ok")
    assert service.verify_key_package(
        package, expected_recipient_peer_id="mallory", expected_scope_id="sess-1", expected_epoch=8
    ) == (False, "unknown_key_share")
    assert service.verify_key_package(
        replace(package, peer_id="mallory"),
        expected_recipient_peer_id="bob",
        expected_scope_id="sess-1",
        expected_epoch=8,
    ) == (False, "key_package_signature_invalid")
    memberships["member-1"] = replace(membership, membership_version=4)
    assert service.verify_key_package(
        package, expected_recipient_peer_id="bob", expected_scope_id="sess-1", expected_epoch=8
    ) == (False, "membership_stale")


def test_unknown_device_substitution_and_reflection_fail_closed() -> None:
    public_key, fingerprint = _device_key()
    membership = PeerMembership("m", "t", "session", "s", "alice", "d", 1, True)
    service = WebrtcPeerIdentityService(
        derive_hub_identity_key(b"h" * 32),
        hub_key_id="hub",
        membership_lookup=lambda _: membership,
        device_fingerprint_lookup=lambda *_: None,
        clock=lambda: 1000,
    )
    with pytest.raises(PeerIdentityError, match="reflection_detected"):
        service.issue_key_package(
            membership_id="m",
            recipient_peer_id="alice",
            epoch=1,
            ecdh_public_key_spki_b64=public_key,
            security_contract_digest="a" * 64,
            expires_at_ms=1_100_000,
        )
    with pytest.raises(PeerIdentityError, match="device_unknown"):
        service.issue_key_package(
            membership_id="m",
            recipient_peer_id="bob",
            epoch=1,
            ecdh_public_key_spki_b64=public_key,
            security_contract_digest="a" * 64,
            expires_at_ms=1_100_000,
        )


def test_confirmation_rolls_back_when_atomic_audit_enqueue_fails(monkeypatch) -> None:
    scope_id = f"atomic-audit-{uuid4()}"
    repository = WebrtcPeerKeyRepository()

    def reject_audit(*_args, **_kwargs):
        raise RuntimeError("audit_enqueue_failed")

    monkeypatch.setattr(SqlSemanticMediaAuditOutbox, "enqueue_in_session", reject_audit)
    with pytest.raises(RuntimeError, match="audit_enqueue_failed"):
        repository.put_confirmation(
            scope_id=scope_id,
            epoch=1,
            sender_peer_id="alice",
            recipient_peer_id="bob",
            package_id="a" * 64,
            confirmation_tag=base64.b64encode(b"t" * 32).decode(),
            expires_at=1300,
            now=1000,
            audit_event=cast(SemanticMediaAuditEvent, object()),
        )
    assert repository.get_confirmation(
        scope_id=scope_id,
        epoch=1,
        sender_peer_id="alice",
        recipient_peer_id="bob",
        now=1000,
    ) is None
