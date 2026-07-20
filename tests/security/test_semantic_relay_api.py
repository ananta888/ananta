from __future__ import annotations

import base64
import hashlib
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent.services.semantic_relay_composition import reset_semantic_relay_service
from agent.services.webrtc_peer_identity_service import spki_fingerprint
from ananta_contracts.webrtc_datachannel import CONTRACT_VERSION, encode_wire_message


def _key() -> tuple[str, str]:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    encoded = base64.b64encode(public).decode()
    return encoded, spki_fingerprint(encoded)


def _jwt(username: str, tenant_id: str) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            "tenant_id": tenant_id,
            "role": "user",
            "iat": now,
            "exp": now + 1800,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _wire(
    *,
    session_id: str,
    sender: str,
    audience: str,
    sequence: int = 1,
    traffic_class: str = "control",
) -> bytes:
    value = b"authenticated-ciphertext"
    return encode_wire_message(
        {
            "version": CONTRACT_VERSION,
            "traffic_class": traffic_class,
            "message_id": f"message-{sequence}",
            "session_id": session_id,
            "epoch": 2,
            "sender_id": sender,
            "audience_id": audience,
            "sequence": sequence,
            "expires_at_ms": int((time.time() + 120) * 1000),
            "compression": "none",
            "security": {"algorithm": "AES-GCM-256", "key_id": "pair-key-1"},
            "payload_bytes": len(value),
            "payload_digest": hashlib.sha256(value).hexdigest(),
            "ciphertext": base64.b64encode(value).decode(),
        }
    )


def _strict_pair(client, admin_auth_header) -> tuple[str, str, dict[str, str]]:
    reset_semantic_relay_service()
    owner_public, owner_fingerprint = _key()
    owner_id = jwt.decode(
        admin_auth_header["Authorization"].split(" ", 1)[1],
        options={"verify_signature": False},
    )["sub"]
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "relay-owner"},
        json={
            "security_contract_version": 1,
            "security_mode": "strict_e2ee",
            "public_key_spki_b64": owner_public,
            "public_key_fingerprint": owner_fingerprint,
            "permissions": {"chat": True},
        },
    )
    assert created.status_code == 201
    session = created.get_json()["session"]
    tenant_id = session["tenant_id"]
    alice_public, alice_fingerprint = _key()
    alice_headers = {
        "Authorization": f"Bearer {_jwt('alice', tenant_id)}",
        "X-Ananta-Device-Id": "relay-alice",
    }
    joined = client.post(
        "/share-sessions/join-by-code",
        headers=alice_headers,
        json={
            "invite_code": session["invite_code"],
            "public_key_spki_b64": alice_public,
            "public_key_fingerprint": alice_fingerprint,
        },
    )
    assert joined.status_code == 201
    owner_packages = client.get(
        f"/share-sessions/{session['id']}/security/key-packages",
        headers=admin_auth_header,
    )
    assert owner_packages.status_code == 200
    package_data = owner_packages.get_json()
    assert package_data["epoch"] == 2
    assert len(package_data["packages"]) == 1
    assert package_data["packages"][0]["peer_id"] == "alice"
    confirmation = client.post(
        f"/share-sessions/{session['id']}/security/key-confirmations",
        headers=admin_auth_header,
        json={
            "recipient_peer_id": "alice",
            "package_id": package_data["packages"][0]["package_id"],
            "epoch": 2,
            "confirmation_tag": base64.b64encode(b"c" * 32).decode(),
        },
    )
    assert confirmation.status_code == 201
    return session["id"], owner_id, alice_headers


def test_semantic_relay_push_poll_ack_and_replay_are_fail_closed(client, admin_auth_header) -> None:
    session_id, owner_id, alice_headers = _strict_pair(client, admin_auth_header)
    wire = _wire(session_id=session_id, sender=owner_id, audience="alice")
    pushed = client.post(
        f"/share-sessions/{session_id}/semantic-relay",
        headers=admin_auth_header,
        data=wire,
        content_type="application/vnd.ananta.webrtc.v1",
    )
    assert pushed.status_code == 201
    assert pushed.get_json()["cursor"] == 1

    duplicate = client.post(
        f"/share-sessions/{session_id}/semantic-relay",
        headers=admin_auth_header,
        data=wire,
        content_type="application/vnd.ananta.webrtc.v1",
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "sequence_duplicate"

    polled = client.get(
        f"/share-sessions/{session_id}/semantic-relay?traffic_class=control&epoch=2&cursor=0",
        headers=alice_headers,
    )
    assert polled.status_code == 200
    page = polled.get_json()
    assert [message["message_id"] for message in page["messages"]] == ["message-1"]
    assert all("tenant_id" not in message for message in page["messages"])

    acknowledged = client.post(
        f"/share-sessions/{session_id}/semantic-relay/ack",
        headers=alice_headers,
        json={"traffic_class": "control", "epoch": 2, "cursor": 1},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.get_json()["acknowledged_cursor"] == 1


def test_semantic_relay_rejects_sender_spoof_feature_off_and_unconfirmed_key(client, admin_auth_header) -> None:
    session_id, owner_id, alice_headers = _strict_pair(client, admin_auth_header)
    spoofed = client.post(
        f"/share-sessions/{session_id}/semantic-relay",
        headers=admin_auth_header,
        data=_wire(session_id=session_id, sender="alice", audience=owner_id),
        content_type="application/vnd.ananta.webrtc.v1",
    )
    assert spoofed.status_code == 403
    assert spoofed.get_json()["error"] == "relay_sender_mismatch"

    visual = _wire(
        session_id=session_id,
        sender=owner_id,
        audience="alice",
        traffic_class="visual_semantic",
    )
    blocked = client.post(
        f"/share-sessions/{session_id}/semantic-relay",
        headers=admin_auth_header,
        data=visual,
        content_type="application/vnd.ananta.webrtc.v1",
    )
    assert blocked.status_code == 403
    assert blocked.get_json()["error"] == "semantic_feature_disabled"

    unconfirmed = _wire(session_id=session_id, sender="alice", audience=owner_id)
    denied = client.post(
        f"/share-sessions/{session_id}/semantic-relay",
        headers=alice_headers,
        data=unconfirmed,
        content_type="application/vnd.ananta.webrtc.v1",
    )
    assert denied.status_code == 409
    assert denied.get_json()["error"] == "key_confirmation_required"
