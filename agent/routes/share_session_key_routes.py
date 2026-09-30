"""Strict-E2EE key package and key confirmation routes of share sessions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import time
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth
from agent.config import settings
from agent.models.webrtc_peer_key_confirmation import (
    WebrtcPeerKeyRepositoryError,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditError
from agent.services.share_security_negotiation_service import (
    ShareSecurityNegotiationError,
    get_share_security_negotiation_service,
)
from agent.services.share_session_service import get_share_session_service
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from agent.services.webrtc_peer_identity_service import (
    PeerIdentityError,
    PeerMembership,
    WebrtcPeerIdentityService,
    derive_hub_identity_key,
)
from agent.routes.share_session_route_support import (
    _current_tenant_id,
    _current_user_id,
    _expected_peer_package_id,
    _is_active_participant,
    _is_session_active,
    _peer_key_repository,
    _strict_e2ee_enabled,
    _strict_security_contract,
)


def _peer_identity_service(
    *, session_id: str, tenant_id: str, memberships: list[dict[str, Any]]
) -> WebrtcPeerIdentityService:
    indexed = {str(item.get("membership_id") or ""): item for item in memberships}

    def membership_lookup(membership_id: str) -> PeerMembership | None:
        item = indexed.get(membership_id)
        if not item:
            return None
        return PeerMembership(
            membership_id=membership_id,
            tenant_id=tenant_id,
            scope_kind="session",
            scope_id=session_id,
            peer_id=str(item.get("peer_id") or ""),
            device_id=str(item.get("device_id") or ""),
            membership_version=int(item.get("membership_version") or 1),
            active=bool(item.get("active")),
        )

    def fingerprint_lookup(peer_id: str, device_id: str) -> str | None:
        for item in memberships:
            if item.get("peer_id") == peer_id and item.get("device_id") == device_id and item.get("active"):
                return str(item.get("fingerprint") or "") or None
        return None

    private_key = derive_hub_identity_key(str(settings.secret_key).encode("utf-8"))
    public_key_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
    return WebrtcPeerIdentityService(
        private_key,
        hub_key_id=f"hub-ed25519:{hashlib.sha256(public_key_b64.encode()).hexdigest()[:16]}",
        membership_lookup=membership_lookup,
        device_fingerprint_lookup=fingerprint_lookup,
    )


@check_user_auth
def list_share_session_key_packages(session_id: str):
    """Return Hub-signed packages addressed to the authenticated member."""
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item) or not _is_active_participant(
        session_id=session_id, user_id=user_id, session_item=session_item
    ):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_not_enabled"}), 409
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    if epoch is None:
        return jsonify({"error": "security_epoch_unavailable"}), 409
    memberships = service.get_security_memberships(session_id)
    local_members = [m for m in memberships if m.get("active") and m.get("peer_id") == user_id]
    if not local_members:
        return jsonify({"error": "membership_stale"}), 403
    tenant_id = str(session_item.get("tenant_id") or "default")[:128]
    if tenant_id != _current_tenant_id():
        return jsonify({"error": "cross_tenant_denied"}), 403
    identity = _peer_identity_service(session_id=session_id, tenant_id=tenant_id, memberships=memberships)
    remote_members = [
        member for member in memberships if member.get("active") and member.get("peer_id") != user_id
    ]
    if not remote_members:
        return jsonify(
            {
                "ok": True,
                "epoch": epoch,
                "tenant_id": tenant_id,
                "security_contract_digest": None,
                "security_contract": None,
                "hub_key_id": identity.hub_key_id,
                "hub_public_key_b64": identity.hub_public_key_b64(),
                "packages": [],
            }
        ), 200
    active_memberships = [member for member in memberships if member.get("active")]
    try:
        if len(active_memberships) == 2:
            contract = _strict_security_contract(
                session_id=session_id,
                session_item=session_item,
                epoch=epoch,
                memberships=memberships,
            )
        else:
            contract = get_share_security_negotiation_service().finalize_strict_group(
                session_id=session_id,
                tenant_id=tenant_id,
                epoch=epoch,
                owner_peer_id=str(session_item.get("owner_user_id") or ""),
                memberships=memberships,
                session_expires_at=session_item.get("expires_at"),
            )
    except ShareSecurityNegotiationError as exc:
        return jsonify({"error": exc.reason_code}), exc.status_code
    contract_digest = str(contract["digest"])
    packages: list[dict[str, Any]] = []
    for member in memberships:
        if not member.get("active") or member.get("peer_id") == user_id:
            continue
        public_key = str(member.get("public_key_spki_b64") or "")
        if not public_key:
            return jsonify({"error": "peer_device_key_missing"}), 409
        try:
            package = identity.issue_key_package(
                membership_id=str(member.get("membership_id") or ""),
                recipient_peer_id=user_id,
                epoch=epoch,
                ecdh_public_key_spki_b64=public_key,
                security_contract_digest=contract_digest,
                expires_at_ms=int((time.time() + 300) * 1000),
            )
        except PeerIdentityError as exc:
            return jsonify({"error": exc.reason_code}), 409
        packages.append(package.__dict__)
    return jsonify(
        {
            "ok": True,
            "epoch": epoch,
            "tenant_id": tenant_id,
            "security_contract_digest": contract_digest,
            "security_contract": contract,
            "hub_key_id": identity.hub_key_id,
            "hub_public_key_b64": identity.hub_public_key_b64(),
            "packages": packages,
        }
    ), 200


@check_user_auth
def put_share_session_key_confirmation(session_id: str):
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if set(body) != {"recipient_peer_id", "package_id", "epoch", "confirmation_tag"}:
        return jsonify({"error": "key_confirmation_fields_invalid"}), 400
    recipient_peer_id = str(body.get("recipient_peer_id") or "")
    package_id = str(body.get("package_id") or "")
    tag = str(body.get("confirmation_tag") or "")
    epoch = body.get("epoch")
    current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch != current_epoch:
        return jsonify({"error": "epoch_mismatch"}), 409
    memberships = service.get_security_memberships(session_id)
    active_memberships = [item for item in memberships if item.get("active")]
    local_members = [item for item in active_memberships if str(item.get("peer_id") or "") == user_id]
    remote_members = [
        item for item in active_memberships if str(item.get("peer_id") or "") == recipient_peer_id
    ]
    if len(local_members) != 1 or len(remote_members) != 1 or recipient_peer_id == user_id:
        return jsonify({"error": "recipient_mismatch"}), 403
    if len(package_id) != 64 or any(char not in "0123456789abcdef" for char in package_id):
        return jsonify({"error": "package_id_invalid"}), 400
    try:
        decoded_tag = base64.b64decode(tag, validate=True)
    except (ValueError, binascii.Error):
        return jsonify({"error": "confirmation_tag_invalid"}), 400
    if len(decoded_tag) != 32:
        return jsonify({"error": "confirmation_tag_invalid"}), 400
    try:
        contract = _strict_security_contract(
            session_id=session_id,
            session_item=session_item,
            epoch=epoch,
            memberships=memberships,
        )
    except ShareSecurityNegotiationError as exc:
        return jsonify({"error": exc.reason_code}), exc.status_code
    remote = remote_members[0]
    expected_package_id = _expected_peer_package_id(
        remote_member=remote,
        recipient_peer_id=user_id,
        epoch=epoch,
        contract_digest=str(contract["digest"]),
    )
    if package_id != expected_package_id:
        return jsonify({"error": "key_package_binding_mismatch"}), 409
    now = time.time()
    audit = current_app.extensions.get("semantic_media_audit_recorder")
    if audit is None:
        return jsonify({"error": "security_audit_unavailable"}), 503
    try:
        audit_event = audit.prepare_transition(
            idempotency_key=(
                f"pair-key-confirm:{session_id}:{epoch}:{user_id}:{recipient_peer_id}:"
                f"{package_id}:{int(now // 120)}"
            ),
            tenant_id=str(session_item.get("tenant_id") or "default"),
            scope=f"session:{session_id}",
            event_type="semantic_rekey",
            transition="pair_key_confirmation",
            reason_code="confirmed",
            epoch=epoch,
            contract_ref=str(contract["digest"]),
        )
        _peer_key_repository.put_confirmation(
            scope_id=session_id,
            epoch=epoch,
            sender_peer_id=user_id,
            recipient_peer_id=recipient_peer_id,
            package_id=package_id,
            confirmation_tag=tag,
            expires_at=now + 300,
            now=now,
            audit_event=audit_event,
        )
    except (SemanticMediaAuditError, WebrtcPeerKeyRepositoryError) as exc:
        status = getattr(exc, "status_code", 409)
        return jsonify({"error": exc.reason_code}), status
    return jsonify({"ok": True}), 201


@check_user_auth
def get_share_session_key_confirmation(session_id: str):
    user_id = _current_user_id()
    sender_peer_id = str(request.args.get("sender_peer_id") or "")
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    if epoch is None:
        return jsonify({"error": "security_epoch_unavailable"}), 409
    active_peers = {
        str(item.get("peer_id") or "")
        for item in service.get_security_memberships(session_id)
        if item.get("active")
    }
    if sender_peer_id == user_id or sender_peer_id not in active_peers:
        return jsonify({"error": "sender_mismatch"}), 403
    row = _peer_key_repository.get_confirmation(
        scope_id=session_id,
        epoch=epoch,
        sender_peer_id=sender_peer_id,
        recipient_peer_id=user_id,
        now=time.time(),
    )
    if row is None:
        return jsonify({"ok": True, "confirmation": None}), 200
    return jsonify(
        {
            "ok": True,
            "confirmation": {
                "sender_peer_id": row.sender_peer_id,
                "recipient_peer_id": row.recipient_peer_id,
                "package_id": row.package_id,
                "epoch": row.epoch,
                "confirmation_tag": row.confirmation_tag,
                "expires_at": row.expires_at,
            },
        }
    ), 200


def register_share_session_key_routes(blueprint: Blueprint) -> None:
    """Register these handlers on the share-session blueprint in their original order."""

    blueprint.add_url_rule(
        "/share-sessions/<session_id>/security/key-packages",
        view_func=list_share_session_key_packages,
        methods=["GET"],
    )
    blueprint.add_url_rule(
        "/share-sessions/<session_id>/security/key-confirmations",
        view_func=put_share_session_key_confirmation,
        methods=["POST"],
    )
    blueprint.add_url_rule(
        "/share-sessions/<session_id>/security/key-confirmations",
        view_func=get_share_session_key_confirmation,
        methods=["GET"],
    )


__all__ = [
    "list_share_session_key_packages",
    "put_share_session_key_confirmation",
    "get_share_session_key_confirmation",
    "register_share_session_key_routes",
]
