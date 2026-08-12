from __future__ import annotations

import base64
import json
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import SemanticMediaAuditOutboxDB
from agent.services.webrtc_peer_identity_service import spki_fingerprint
from ananta_contracts.webrtc_security import (
    AuthenticatedMetadata,
    EnvelopeRecipient,
    EnvelopeScope,
    SecureEnvelopeV1,
    seal_secure_envelope,
)
from ananta_contracts.webrtc_security_negotiation import (
    parse_security_proposal,
    security_contract_digest,
)


def _device_key() -> tuple[str, str]:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    encoded = base64.b64encode(public).decode()
    return encoded, spki_fingerprint(encoded)


def _user_jwt(username: str) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {"sub": username, "tenant_id": "admin", "role": "user", "iat": now, "exp": now + 1800},
        settings.secret_key,
        algorithm="HS256",
    )


def test_strict_session_packages_confirmation_and_replay_gate(client, admin_auth_header) -> None:
    owner_public, owner_fingerprint = _device_key()
    owner_id = jwt.decode(admin_auth_header["Authorization"].split(" ", 1)[1], options={"verify_signature": False})[
        "sub"
    ]
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "owner-device"},
        json={
            "title": "strict pair",
            "security_contract_version": 1,
            "security_mode": "strict_e2ee",
            "public_key_spki_b64": owner_public,
            "public_key_fingerprint": owner_fingerprint,
            "permissions": {"chat": True, "view_tui": True, "remote_cursor": True},
        },
    )
    assert created.status_code == 201
    session = created.get_json()["session"]
    session_id = session["id"]
    alice_public, alice_fingerprint = _device_key()
    alice_headers = {"Authorization": f"Bearer {_user_jwt('alice')}", "X-Ananta-Device-Id": "alice-device"}
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
    assert joined.get_json()["session"]["security_epoch"] == 2

    owner_packages = client.get(f"/share-sessions/{session_id}/security/key-packages", headers=admin_auth_header)
    assert owner_packages.status_code == 200
    package_data = owner_packages.get_json()
    assert package_data["packages"][0]["peer_id"] == "alice"
    assert package_data["packages"][0]["recipient_peer_id"] == owner_id
    assert package_data["packages"][0]["security_contract_digest"] == package_data["security_contract_digest"]
    final_contract = package_data["security_contract"]
    assert final_contract["digest"] == security_contract_digest(
        parse_security_proposal(final_contract["offer"]),
        parse_security_proposal(final_contract["answer"]),
    )
    assert final_contract["offer"]["minimum_mode"] == "strict_e2ee"
    assert final_contract["answer"]["selected_mode"] == "strict_e2ee"
    assert final_contract["offer"]["key_epoch"] == 2
    assert "private" not in json.dumps(package_data).lower()

    alice_packages = client.get(
        f"/share-sessions/{session_id}/security/key-packages", headers=alice_headers
    ).get_json()

    tag = base64.b64encode(b"t" * 32).decode()
    forged = client.post(
        f"/share-sessions/{session_id}/security/key-confirmations",
        headers=alice_headers,
        json={
            "recipient_peer_id": owner_id,
            "package_id": "0" * 64,
            "epoch": 2,
            "confirmation_tag": tag,
        },
    )
    assert forged.status_code == 409
    assert forged.get_json()["error"] == "key_package_binding_mismatch"
    posted = client.post(
        f"/share-sessions/{session_id}/security/key-confirmations",
        headers=alice_headers,
        json={
            "recipient_peer_id": owner_id,
            "package_id": alice_packages["packages"][0]["package_id"],
            "epoch": 2,
            "confirmation_tag": tag,
        },
    )
    assert posted.status_code == 201
    with Session(engine) as db:
        audit_rows = db.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.event_type == "semantic_rekey",
                SemanticMediaAuditOutboxDB.transition == "pair_key_confirmation",
                SemanticMediaAuditOutboxDB.epoch == 2,
            )
        ).all()
    assert audit_rows
    confirmation = client.get(
        f"/share-sessions/{session_id}/security/key-confirmations?sender_peer_id=alice",
        headers=admin_auth_header,
    ).get_json()["confirmation"]
    assert confirmation["confirmation_tag"] == tag

    now_ms = int(time.time() * 1000)
    pending = SecureEnvelopeV1(
        version=1,
        scope=EnvelopeScope("session", session_id),
        sender_id=owner_id,
        recipient=EnvelopeRecipient("peer", "alice"),
        epoch=2,
        sequence=1,
        key_id="pair-key-1",
        payload_type="pair.view_delta",
        expires_at_ms=now_ms + 60_000,
        nonce_b64=base64.b64encode(b"n" * 12).decode(),
        aad=AuthenticatedMetadata("semantic", "json", package_data["security_contract_digest"]),
        ciphertext_b64="",
    )
    sealed = seal_secure_envelope(key=b"k" * 32, plaintext=b'{"secret":"not-visible-to-hub"}', envelope=pending)
    relay_body = {
        "message_id": "secure-1",
        "encrypted_payload": json.dumps(sealed.to_dict()),
    }
    not_confirmed = client.post(
        f"/share-sessions/{session_id}/view/push", headers=admin_auth_header, json=relay_body
    )
    assert not_confirmed.status_code == 409
    assert not_confirmed.get_json()["error"] == "bidirectional_key_confirmation_required"

    owner_confirmation = client.post(
        f"/share-sessions/{session_id}/security/key-confirmations",
        headers=admin_auth_header,
        json={
            "recipient_peer_id": "alice",
            "package_id": package_data["packages"][0]["package_id"],
            "epoch": 2,
            "confirmation_tag": base64.b64encode(b"o" * 32).decode(),
        },
    )
    assert owner_confirmation.status_code == 201
    first = client.post(f"/share-sessions/{session_id}/view/push", headers=admin_auth_header, json=relay_body)
    assert first.status_code == 200
    duplicate = client.post(f"/share-sessions/{session_id}/view/push", headers=admin_auth_header, json=relay_body)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "sequence_duplicate"

    participant_delta = SecureEnvelopeV1(
        version=1,
        scope=EnvelopeScope("session", session_id),
        sender_id="alice",
        recipient=EnvelopeRecipient("peer", owner_id),
        epoch=2,
        sequence=1,
        key_id="pair-key-1",
        payload_type="pair.view_delta",
        expires_at_ms=now_ms + 60_000,
        nonce_b64=base64.b64encode(b"a" * 12).decode(),
        aad=AuthenticatedMetadata("semantic", "json", package_data["security_contract_digest"]),
        ciphertext_b64="",
    )
    sealed_participant_delta = seal_secure_envelope(
        key=b"k" * 32,
        plaintext=b'{"kind":"snapshot","route":"/goals"}',
        envelope=participant_delta,
    )
    participant_delta_sent = client.post(
        f"/share-sessions/{session_id}/view/push",
        headers=alice_headers,
        json={
            "message_id": "participant-view-1",
            "encrypted_payload": json.dumps(sealed_participant_delta.to_dict()),
        },
    )
    assert participant_delta_sent.status_code == 200

    snapshot_request = SecureEnvelopeV1(
        version=1,
        scope=EnvelopeScope("session", session_id),
        sender_id="alice",
        recipient=EnvelopeRecipient("peer", owner_id),
        epoch=2,
        sequence=1,
        key_id="pair-key-1",
        payload_type="pair.snapshot_request",
        expires_at_ms=now_ms + 60_000,
        nonce_b64=base64.b64encode(b"r" * 12).decode(),
        aad=AuthenticatedMetadata("control", "json", package_data["security_contract_digest"]),
        ciphertext_b64="",
    )
    sealed_snapshot_request = seal_secure_envelope(
        key=b"k" * 32,
        plaintext=b'{"reason":"base_hash_mismatch"}',
        envelope=snapshot_request,
    )
    snapshot_request_sent = client.post(
        f"/share-sessions/{session_id}/view/push",
        headers=alice_headers,
        json={
            "message_id": "participant-snapshot-request-1",
            "encrypted_payload": json.dumps(sealed_snapshot_request.to_dict()),
        },
    )
    assert snapshot_request_sent.status_code == 200

    def post_participant_payload(
        *,
        message_id: str,
        payload_type: str,
        traffic_class: str,
        sequence: int,
        nonce_byte: bytes,
    ):
        candidate = SecureEnvelopeV1(
            version=1,
            scope=EnvelopeScope("session", session_id),
            sender_id="alice",
            recipient=EnvelopeRecipient("peer", owner_id),
            epoch=2,
            sequence=sequence,
            key_id="pair-key-1",
            payload_type=payload_type,
            expires_at_ms=now_ms + 60_000,
            nonce_b64=base64.b64encode(nonce_byte * 12).decode(),
            aad=AuthenticatedMetadata(
                traffic_class,
                "json",
                package_data["security_contract_digest"],
            ),
            ciphertext_b64="",
        )
        sealed_candidate = seal_secure_envelope(
            key=b"k" * 32,
            plaintext=b'{"denied":true}',
            envelope=candidate,
        )
        return client.post(
            f"/share-sessions/{session_id}/view/push",
            headers=alice_headers,
            json={
                "message_id": message_id,
                "encrypted_payload": json.dumps(sealed_candidate.to_dict()),
            },
        )

    denied_control = post_participant_payload(
        message_id="participant-control-denied",
        payload_type="pair.control",
        traffic_class="control",
        sequence=2,
        nonce_byte=b"c",
    )
    assert denied_control.status_code == 403
    assert denied_control.get_json()["error"] == "payload_permission_required"

    denied_artifact = post_participant_payload(
        message_id="participant-artifact-denied",
        payload_type="pair.artifact_ref",
        traffic_class="semantic",
        sequence=2,
        nonce_byte=b"f",
    )
    assert denied_artifact.status_code == 403
    assert denied_artifact.get_json()["error"] == "payload_permission_required"

    wrong_view_traffic = post_participant_payload(
        message_id="participant-view-wrong-traffic",
        payload_type="pair.view_delta",
        traffic_class="control",
        sequence=3,
        nonce_byte=b"w",
    )
    assert wrong_view_traffic.status_code == 403
    assert wrong_view_traffic.get_json()["error"] == "traffic_class_mismatch"

    owner_view = client.get(f"/share-sessions/{session_id}/view/poll", headers=admin_auth_header)
    assert owner_view.status_code == 200
    relayed_view = owner_view.get_json()["view_messages"]
    assert [item["message_id"] for item in relayed_view] == [
        "participant-view-1",
        "participant-snapshot-request-1",
    ]
    assert all(set(item) == {"message_id", "encrypted_payload"} for item in relayed_view)
    assert "goals" not in json.dumps(relayed_view)

    oversized = client.post(
        f"/share-sessions/{session_id}/view/push",
        headers=alice_headers,
        json={"message_id": "oversized", "encrypted_payload": "x" * (256 * 1024 + 1)},
    )
    assert oversized.status_code == 413
    assert oversized.get_json()["error"] == "payload_too_large"

    canary = "STRICT_CHAT_CANARY_MUST_NEVER_REACH_HUB_OR_RELAY"
    chat_pending = SecureEnvelopeV1(
        version=1,
        scope=EnvelopeScope("session", session_id),
        sender_id=owner_id,
        recipient=EnvelopeRecipient("peer", "alice"),
        epoch=2,
        sequence=2,
        key_id="pair-key-1",
        payload_type="pair.chat_message",
        expires_at_ms=now_ms + 60_000,
        nonce_b64=base64.b64encode(b"c" * 12).decode(),
        aad=AuthenticatedMetadata("semantic", "json", package_data["security_contract_digest"]),
        ciphertext_b64="",
    )
    sealed_chat = seal_secure_envelope(
        key=b"k" * 32,
        plaintext=json.dumps({"text": canary}).encode(),
        envelope=chat_pending,
    )
    sent_chat = client.post(
        f"/share-sessions/{session_id}/chat/messages",
        headers=admin_auth_header,
        json={"id": "chat-secure-1", "encrypted_payload": json.dumps(sealed_chat.to_dict())},
    )
    assert sent_chat.status_code == 201
    relayed_chat = client.get(
        f"/share-sessions/{session_id}/chat/messages", headers=alice_headers
    )
    assert relayed_chat.status_code == 200
    relayed_document = relayed_chat.get_json()
    assert canary not in json.dumps(relayed_document)
    assert relayed_document["messages"]
    assert set(relayed_document["messages"][0]) == {"id", "encrypted_payload"}

    plaintext_attempt = client.post(
        f"/share-sessions/{session_id}/chat/messages",
        headers=admin_auth_header,
        json={"id": "plaintext", "text": canary},
    )
    assert plaintext_attempt.status_code == 400
    assert plaintext_attempt.get_json()["error"] == "strict_envelope_fields_invalid"


def test_strict_session_rejects_missing_or_substituted_device_key(client, admin_auth_header) -> None:
    missing = client.post(
        "/share-sessions",
        headers=admin_auth_header,
        json={"security_contract_version": 1, "security_mode": "strict_e2ee"},
    )
    assert missing.status_code == 400
    assert missing.get_json()["error"] == "device_key_required"
    public, _ = _device_key()
    substituted = client.post(
        "/share-sessions",
        headers=admin_auth_header,
        json={
            "security_contract_version": 1,
            "security_mode": "strict_e2ee",
            "public_key_spki_b64": public,
            "public_key_fingerprint": "0" * 64,
        },
    )
    assert substituted.status_code == 400
    assert substituted.get_json()["error"] == "device_key_substitution"


def test_strict_group_key_packages_bind_all_active_hub_memberships(client, admin_auth_header) -> None:
    owner_public, owner_fingerprint = _device_key()
    created = client.post(
        "/share-sessions",
        headers={**admin_auth_header, "X-Ananta-Device-Id": "group-owner-device"},
        json={
            "title": "strict group",
            "mode": "group",
            "transport": "semantic_sfu",
            "security_contract_version": 1,
            "security_mode": "strict_e2ee",
            "public_key_spki_b64": owner_public,
            "public_key_fingerprint": owner_fingerprint,
            "permissions": {"chat": True, "view_tui": True},
        },
    )
    assert created.status_code == 201
    session = created.get_json()["session"]
    participants: list[tuple[str, dict[str, str]]] = []
    for peer_id in ("group-alice", "group-bob"):
        public, fingerprint = _device_key()
        headers = {
            "Authorization": f"Bearer {_user_jwt(peer_id)}",
            "X-Ananta-Device-Id": f"{peer_id}-device",
        }
        joined = client.post(
            "/share-sessions/join-by-code",
            headers=headers,
            json={
                "invite_code": session["invite_code"],
                "public_key_spki_b64": public,
                "public_key_fingerprint": fingerprint,
            },
        )
        assert joined.status_code == 201
        participants.append((peer_id, headers))

    response = client.get(
        f"/share-sessions/{session['id']}/security/key-packages",
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    body = response.get_json()
    contract = body["security_contract"]
    assert contract["kind"] == "strict_group"
    assert contract["authorization"] == "hub_signed_peer_packages"
    assert contract["key_epoch"] == 3
    assert contract["digest"] == body["security_contract_digest"]
    assert {row["peer_id"] for row in contract["members"]} >= {
        "group-alice",
        "group-bob",
    }
    assert len(contract["members"]) == 3
    assert len(body["packages"]) == 2
    assert all(package["security_contract_digest"] == contract["digest"] for package in body["packages"])
    assert "private" not in json.dumps(body).lower()

    alice_response = client.get(
        f"/share-sessions/{session['id']}/security/key-packages",
        headers=participants[0][1],
    )
    assert alice_response.status_code == 200
    assert len(alice_response.get_json()["packages"]) == 2
