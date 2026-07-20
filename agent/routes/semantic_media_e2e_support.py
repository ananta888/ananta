"""Ephemeral identity/session/consent seed for the live semantic-media gate.

This blueprint is imported and registered only when both the generic auth test
surface and the explicit live semantic-media E2E mode are enabled. It prepares
authorities needed by two browser processes but never creates offers, transfer
records, curation decisions, receipts, tasks or datasets.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Blueprint, jsonify, request

from agent.auth import admin_required
from agent.config import settings
from agent.repositories.webrtc_peer_key_repository import WebrtcPeerKeyRepository
from agent.services.share_session_service import get_share_session_service
from agent.services.speech_evidence_consent_service import (
    get_speech_evidence_consent_service,
)
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.webrtc_peer_identity_service import spki_fingerprint

semantic_media_e2e_support_bp = Blueprint("semantic_media_e2e_support", __name__)
_GROUP_ROLES = (
    "publisher",
    "ordinary-receiver",
    "semantic-receiver",
    "validator",
    "weak-receiver",
    "removed-member",
    "late-join",
)


@semantic_media_e2e_support_bp.post("/test/semantic-media/group-seed")
@admin_required
def seed_semantic_media_group():
    """Create identities only; all SFU/key authority stays on product routes.

    Browser contexts generate their non-extractable device keys before this
    call.  The fixture registers only the public bindings and returns ordinary
    short-lived Hub user credentials.  It never receives the LiveKit secret,
    issues an SFU token, prepares a group epoch or creates a compute lease.
    """

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or set(body) != {"participants"}:
        return jsonify({"ok": False, "error": "semantic_media_group_seed_shape_invalid"}), 400
    raw_participants = body.get("participants")
    if not isinstance(raw_participants, list) or len(raw_participants) != len(_GROUP_ROLES):
        return jsonify({"ok": False, "error": "semantic_media_group_seed_participants_invalid"}), 422
    provided: dict[str, tuple[str, str]] = {}
    for raw in raw_participants:
        if not isinstance(raw, dict) or set(raw) != {"role", "public_key_spki_b64", "fingerprint"}:
            return jsonify({"ok": False, "error": "semantic_media_group_seed_participant_invalid"}), 400
        role = str(raw.get("role") or "")
        public_key = str(raw.get("public_key_spki_b64") or "")
        fingerprint = str(raw.get("fingerprint") or "")
        if role not in _GROUP_ROLES or role in provided:
            return jsonify({"ok": False, "error": "semantic_media_group_seed_role_invalid"}), 422
        try:
            calculated = spki_fingerprint(public_key)
        except Exception:
            return jsonify({"ok": False, "error": "semantic_media_group_seed_key_invalid"}), 422
        if calculated != fingerprint:
            return jsonify({"ok": False, "error": "semantic_media_group_seed_key_invalid"}), 422
        provided[role] = (public_key, fingerprint)
    if set(provided) != set(_GROUP_ROLES):
        return jsonify({"ok": False, "error": "semantic_media_group_seed_roles_incomplete"}), 422

    suffix = uuid.uuid4().hex
    tenant_id = f"semantic-group-e2e-{suffix}"
    identities = {role: f"{role}-{suffix}" for role in _GROUP_ROLES}
    devices = {role: f"device-{role}-{suffix}" for role in _GROUP_ROLES}
    service = get_share_session_service()
    owner_key, owner_fingerprint = provided["publisher"]
    session = service.create_session(
        owner_user_id=identities["publisher"],
        owner_device_id=devices["publisher"],
        title="Semantic media bounded-group live E2E",
        mode="group",
        transport="semantic_sfu",
        permissions={"chat": True, "view_tui": True, "artifact_share": True},
        expires_at=time.time() + 15 * 60,
        security_contract_version=1,
        security_mode="strict_e2ee",
        owner_public_key_spki_b64=owner_key,
        owner_public_key_fingerprint=owner_fingerprint,
        tenant_id=tenant_id,
    )
    session_id = str(session.get("id") or "")
    invite_code = str(session.get("invite_code") or "")
    if not session_id or not invite_code:
        return jsonify({"ok": False, "error": "semantic_media_group_seed_session_failed"}), 503
    for role in _GROUP_ROLES[1:]:
        public_key, fingerprint = provided[role]
        joined = service.join_session(
            session_id=session_id,
            user_id=identities[role],
            device_id=devices[role],
            public_key_fingerprint=fingerprint,
            invite_code=invite_code,
            public_key_spki_b64=public_key,
            tenant_id=tenant_id,
        )
        if not joined.ok:
            return jsonify({"ok": False, "error": "semantic_media_group_seed_join_failed"}), 503
    current = service.get_session(session_id) or {}
    epoch = int(current.get("security_epoch") or 0)
    if epoch < 1:
        return jsonify({"ok": False, "error": "semantic_media_group_seed_epoch_unavailable"}), 503
    participant_ids = {
        str(row.get("user_id") or ""): str(row.get("id") or "")
        for row in service.get_participants(session_id)
        if row.get("revoked_at") is None
    }
    expires_at = int(time.time()) + 10 * 60
    return jsonify(
        {
            "ok": True,
            "data": {
                "hub_url": request.host_url.rstrip("/"),
                "tenant_id": tenant_id,
                "session_id": session_id,
                "membership_epoch": epoch,
                "participants": {
                    role: {
                        "user_id": identity,
                        "device_id": devices[role],
                        "participant_id": participant_ids.get(identity),
                        "user_token": _token(identity, tenant_id, expires_at),
                    }
                    for role, identity in identities.items()
                },
                "expires_at_ms": expires_at * 1000,
            },
        }
    ), 201


@semantic_media_e2e_support_bp.post("/test/semantic-media/peer-sync-seed")
@admin_required
def seed_semantic_media_peer_sync():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or set(body) - {"transport"}:
        return jsonify({"ok": False, "error": "semantic_media_e2e_seed_shape_invalid"}), 400
    transport = str(body.get("transport") or "hub_relay")
    if transport not in {"hub_relay", "webrtc"}:
        return jsonify({"ok": False, "error": "semantic_media_e2e_seed_transport_invalid"}), 422
    now_ms = time.time_ns() // 1_000_000
    suffix = uuid.uuid4().hex
    tenant_id = f"semantic-e2e-{suffix}"
    sender_id = f"peer-a-{suffix}"
    recipient_id = f"peer-b-{suffix}"
    sender_public_key, sender_fingerprint = _device_key()
    recipient_public_key, recipient_fingerprint = _device_key()
    session_service = get_share_session_service()
    session = session_service.create_session(
        owner_user_id=sender_id,
        owner_device_id=f"device-a-{suffix}",
        title="Semantic media live E2E",
        mode="relay",
        transport=transport,
        permissions={"artifact_share": True},
        expires_at=time.time() + 15 * 60,
        security_contract_version=1,
        security_mode="strict_e2ee",
        owner_public_key_spki_b64=sender_public_key,
        owner_public_key_fingerprint=sender_fingerprint,
        tenant_id=tenant_id,
    )
    joined = session_service.join_session(
        session_id=str(session["id"]),
        user_id=recipient_id,
        device_id=f"device-b-{suffix}",
        public_key_fingerprint=recipient_fingerprint,
        invite_code=str(session["invite_code"]),
        public_key_spki_b64=recipient_public_key,
        tenant_id=tenant_id,
    )
    if not joined.ok:
        return jsonify({"ok": False, "error": "semantic_media_e2e_seed_join_failed"}), 503
    current = session_service.get_session(str(session["id"]))
    epoch = int((current or {}).get("security_epoch") or 0)
    if epoch < 1:
        return jsonify({"ok": False, "error": "semantic_media_e2e_seed_epoch_unavailable"}), 503
    confirmations = WebrtcPeerKeyRepository()
    for sender, recipient in ((sender_id, recipient_id), (recipient_id, sender_id)):
        confirmations.put_confirmation(
            scope_id=str(session["id"]),
            epoch=epoch,
            sender_peer_id=sender,
            recipient_peer_id=recipient,
            package_id=_digest(f"package:{sender}:{recipient}:{suffix}"),
            confirmation_tag=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
            expires_at=time.time() + 10 * 60,
        )

    signatures = {
        sender_id: _digest(f"signature:{sender_id}:{suffix}"),
        recipient_id: _digest(f"signature:{recipient_id}:{suffix}"),
    }
    consent_service = get_speech_evidence_consent_service()
    consents = []
    for owner in (sender_id, recipient_id):
        consent = consent_service.grant(
            VoicePrincipal(tenant_id, owner),
            {
                "schema": "ananta.speech-evidence-consent.v1",
                "consent_id": f"consent-{owner}",
                "tenant_id": tenant_id,
                "owner_subject": owner,
                "speaker_id": sender_id,
                "recipient_id": recipient_id,
                "direction": "sender_to_receiver",
                "pair_id": str(session["id"]),
                "session_id": str(session["id"]),
                "session_epoch": epoch,
                "purpose": "speech_dataset_curation",
                "data_classes": ["transcript", "correction"],
                "retention_seconds": 3_600,
                "trainer_locations": ["local_hub"],
                "grants": {
                    "capture": True,
                    "transcript_share": True,
                    "feature_share": False,
                    "raw_audio_share": False,
                    "dataset_import": True,
                    "training": True,
                    "inference": False,
                    "export": False,
                },
                "consent_version": 1,
                "revocation_epoch": 0,
                "issued_at_ms": now_ms,
                "expires_at_ms": now_ms + 10 * 60_000,
                "state": "active",
                "required_signers": sorted(signatures),
                "signatures": signatures,
            },
        )
        consents.append(consent)

    sender_consent = next(value for value in consents if value.owner_subject == sender_id)
    recipient_consent = next(value for value in consents if value.owner_subject == recipient_id)
    expires_at = int(time.time()) + 10 * 60
    return jsonify(
        {
            "ok": True,
            "data": {
                "hub_url": request.host_url.rstrip("/"),
                "tenant_id": tenant_id,
                "session_id": str(session["id"]),
                "epoch": epoch,
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "sender_token": _token(sender_id, tenant_id, expires_at),
                "recipient_token": _token(recipient_id, tenant_id, expires_at),
                "sender_consent_digest": sender_consent.consent_digest,
                "recipient_consent_digest": recipient_consent.consent_digest,
                "scope_digest": sender_consent.scope_digest,
                "consent_version": 1,
                "expires_at_ms": min(sender_consent.expires_at_ms, recipient_consent.expires_at_ms),
                "transport": transport,
            },
        }
    ), 201


def _token(subject: str, tenant_id: str, expires_at: int) -> str:
    issued_at = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "tenant_id": tenant_id,
            "role": "user",
            "mfa_enabled": False,
            "iat": issued_at,
            "exp": expires_at,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _device_key() -> tuple[str, str]:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    encoded = base64.b64encode(public).decode("ascii")
    return encoded, spki_fingerprint(encoded)


__all__ = ["semantic_media_e2e_support_bp"]
