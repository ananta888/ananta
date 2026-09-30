"""Share-session lifecycle routes; key, relay, view and chat routes register from sibling modules."""

from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.services.share_audit_service import (
    audit_participant_joined,
    audit_participant_revoked,
    audit_permission_changed,
    audit_session_created,
)
from agent.services.share_relay_compatibility_service import (
    get_share_relay_compatibility_service,
)
from agent.services.share_session_permissions import PermissionContractError
from agent.services.share_session_service import get_share_session_service
from agent.services.webrtc_peer_identity_service import PeerIdentityError
from agent.routes.share_session_route_support import (
    _current_device_id,
    _current_tenant_id,
    _current_user_id,
    _is_active_participant,
    _is_session_active,
    _participant_last_seen,
    _peer_key_repository,
    _strict_e2ee_enabled,
    _view_started_audited,
)
from agent.routes.share_session_key_routes import register_share_session_key_routes
from agent.routes.share_session_relay_routes import register_share_session_relay_routes
from agent.routes.share_session_view_routes import register_share_session_view_routes
from agent.routes.share_session_chat_routes import register_share_session_chat_routes


share_sessions_bp = Blueprint("share_sessions", __name__)


@share_sessions_bp.route("/share-sessions", methods=["POST"])
@check_user_auth
def create_share_session():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    owner_device_id = str(body.get("owner_device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
    service = get_share_session_service()
    try:
        session_item = service.create_session(
            owner_user_id=user_id,
            owner_device_id=owner_device_id,
            title=str(body.get("title") or "Shared Session").strip() or "Shared Session",
            mode=str(body.get("mode") or "relay").strip() or "relay",
            transport=str(body.get("transport") or "hub_relay").strip() or "hub_relay",
            permissions=body.get("permissions") if body.get("permissions") is not None else {},
            expires_at=float(body["expires_at"]) if isinstance(body.get("expires_at"), (int, float)) else None,
            security_contract_version=body.get("security_contract_version", 0),
            security_mode=str(body.get("security_mode") or "legacy"),
            owner_public_key_spki_b64=str(body.get("public_key_spki_b64") or ""),
            owner_public_key_fingerprint=str(body.get("public_key_fingerprint") or ""),
            tenant_id=_current_tenant_id(),
        )
    except (PermissionContractError, PeerIdentityError) as exc:
        return jsonify({"error": exc.reason_code, "field": getattr(exc, "field", None)}), 400
    audit_session_created(
        session_id=str(session_item.get("id") or ""),
        owner_user_id=user_id,
        owner_device_id=owner_device_id,
        mode=str(session_item.get("mode") or ""),
        transport=str(session_item.get("transport") or ""),
        permissions=dict(session_item.get("permissions") or {}),
    )
    return jsonify({"ok": True, "session": session_item, "data": session_item}), 201


@share_sessions_bp.route("/share-sessions", methods=["GET"])
@check_user_auth
def list_share_sessions():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    items = service.list_sessions_for_owner(user_id)
    return jsonify({"ok": True, "sessions": items, "data": {"items": items}}), 200


@share_sessions_bp.route("/share-sessions/joined", methods=["GET"])
@check_user_auth
def list_joined_share_sessions():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    items = service.list_sessions_as_participant(user_id)
    return jsonify({"ok": True, "sessions": items, "data": {"items": items}}), 200


@share_sessions_bp.route("/share-sessions/join-by-code", methods=["POST"])
@check_user_auth
def join_share_session_by_code():
    """Join a session using only an invite_code — no session_id required."""
    auth = dict(get_request_auth_context() or {})
    user_id = str(auth.get("sub") or auth.get("username") or "").strip()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    invite_code = str(body.get("invite_code") or "").strip()
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    device_id = str(body.get("device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
    fingerprint = str(body.get("public_key_fingerprint") or "").strip()
    public_key_spki_b64 = str(body.get("public_key_spki_b64") or "").strip()
    service = get_share_session_service()
    session_item = service.get_session_by_invite_code(invite_code)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    minimum_security_mode = str(body.get("minimum_security_mode") or "legacy")
    if minimum_security_mode not in {"legacy", "strict_e2ee"}:
        return jsonify({"error": "minimum_security_mode_invalid"}), 400
    if minimum_security_mode == "strict_e2ee" and not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "security_downgrade_rejected"}), 409
    session_id = str(session_item.get("id") or "")
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
        public_key_spki_b64=public_key_spki_b64,
        tenant_id=_current_tenant_id(),
    )
    if not joined.ok:
        code_map = {
            "session_not_found": 404,
            "invalid_invite": 403,
            "session_revoked": 403,
            "session_expired": 403,
            "cross_tenant_denied": 403,
        }
        return jsonify({"error": joined.reason or "join_failed"}), code_map.get(joined.reason or "", 400)
    participant = dict(joined.participant or {})
    _participant_last_seen[str(participant.get("id") or "")] = time.time()
    audit_participant_joined(
        session_id=session_id,
        participant_id=str(participant.get("id") or ""),
        user_id=user_id,
        device_id=str(participant.get("device_id") or ""),
        public_key_fingerprint=str(participant.get("public_key_fingerprint") or ""),
        permissions=dict(participant.get("permissions") or {}),
    )
    # Membership changes rotate the authoritative epoch; return the refreshed
    # session rather than the pre-join snapshot.
    session_item = service.get_session(session_id) or session_item
    return jsonify({"ok": True, "session": session_item, "participant": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/participants", methods=["GET"])
@check_user_auth
def list_share_session_participants(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    raw = service.get_participants(session_id)
    participants = []
    for p in raw:
        entry = dict(p)
        entry["last_seen_at"] = _participant_last_seen.get(str(p.get("id") or ""))
        participants.append(entry)
    # Include owner as synthetic participant entry
    owner_id = str(session_item.get("owner_user_id") or "")
    if owner_id and not any(str(p.get("user_id") or "") == owner_id for p in raw):
        participants.insert(
            0,
            {
                "id": f"owner-{owner_id}",
                "user_id": owner_id,
                "device_id": str(session_item.get("owner_device_id") or ""),
                "role": "owner",
                "permissions": dict(session_item.get("permissions") or {}),
                "joined_at": float(session_item.get("created_at") or 0),
                "revoked_at": None,
                "last_seen_at": _participant_last_seen.get(f"owner-{owner_id}"),
            },
        )
    return jsonify({"ok": True, "participants": participants}), 200


# Strict-E2EE key and semantic relay routes keep their original registration order.
register_share_session_key_routes(share_sessions_bp)
register_share_session_relay_routes(share_sessions_bp)


@share_sessions_bp.route("/share-sessions/<session_id>", methods=["DELETE"])
@check_user_auth
def delete_share_session(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if str(session_item.get("owner_user_id") or "") != user_id:
        return jsonify({"error": "forbidden"}), 403
    service.revoke_session(session_id=session_id, actor_user_id=user_id)
    get_share_relay_compatibility_service().clear_session(
        tenant_id=str(session_item.get("tenant_id") or "default"),
        session_id=session_id,
    )
    _view_started_audited.discard(session_id)
    _peer_key_repository.delete_scope(session_id)
    return jsonify({"ok": True}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/heartbeat", methods=["POST"])
@check_user_auth
def share_session_heartbeat(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict) or not _is_session_active(session_item):
        return jsonify({"error": "session_not_found"}), 404
    if str(session_item.get("owner_user_id") or "") == user_id:
        _participant_last_seen[f"owner-{user_id}"] = time.time()
    else:
        participants = service.get_participants(session_id)
        for p in participants:
            if str(p.get("user_id") or "") == user_id and not p.get("revoked_at"):
                _participant_last_seen[str(p.get("id") or "")] = time.time()
                break
    return jsonify({"ok": True}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/join", methods=["POST"])
@check_user_auth
def join_share_session(session_id: str):
    auth = dict(get_request_auth_context() or {})
    user_id = str(auth.get("sub") or auth.get("username") or "").strip()
    if not str(auth.get("sub") or "").strip():
        return jsonify({"error": "oidc_context_required"}), 403
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    device_id = str(body.get("device_id") or _current_device_id() or "").strip()
    invite_code = str(body.get("invite_code") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id_required"}), 400
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    fingerprint = str(body.get("public_key_fingerprint") or "").strip()
    public_key_spki_b64 = str(body.get("public_key_spki_b64") or "").strip()
    service = get_share_session_service()
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
        public_key_spki_b64=public_key_spki_b64,
        tenant_id=_current_tenant_id(),
    )
    if not joined.ok:
        if joined.reason in {"session_not_found"}:
            return jsonify({"error": joined.reason}), 404
        if joined.reason in {"invalid_invite", "session_revoked", "session_expired", "cross_tenant_denied"}:
            return jsonify({"error": joined.reason}), 403
        return jsonify({"error": joined.reason or "join_failed"}), 400
    participant = dict(joined.participant or {})
    audit_participant_joined(
        session_id=session_id,
        participant_id=str(participant.get("id") or ""),
        user_id=user_id,
        device_id=str(participant.get("device_id") or ""),
        public_key_fingerprint=str(participant.get("public_key_fingerprint") or ""),
        permissions=dict(participant.get("permissions") or {}),
    )
    return jsonify({"ok": True, "data": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/participants/join", methods=["POST"])
@check_user_auth
def join_share_session_participant(session_id: str):
    """Compatibility join endpoint for hub-relay clients that already know the session id."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    device_id = str(body.get("device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
    invite_code = str(body.get("invite_code") or session_item.get("invite_code") or "").strip()
    fingerprint = str(body.get("public_key_fingerprint") or "").strip()
    public_key_spki_b64 = str(body.get("public_key_spki_b64") or "").strip()
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
        public_key_spki_b64=public_key_spki_b64,
        tenant_id=_current_tenant_id(),
    )
    if not joined.ok:
        if joined.reason == "session_not_found":
            return jsonify({"error": joined.reason}), 404
        if joined.reason in {"invalid_invite", "session_revoked", "session_expired", "cross_tenant_denied"}:
            return jsonify({"error": joined.reason}), 403
        return jsonify({"error": joined.reason or "join_failed"}), 400
    participant = dict(joined.participant or {})
    _participant_last_seen[str(participant.get("id") or "")] = time.time()
    audit_participant_joined(
        session_id=session_id,
        participant_id=str(participant.get("id") or ""),
        user_id=user_id,
        device_id=str(participant.get("device_id") or ""),
        public_key_fingerprint=str(participant.get("public_key_fingerprint") or ""),
        permissions=dict(participant.get("permissions") or {}),
    )
    return jsonify({"ok": True, "participant": participant, "data": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/permissions", methods=["PATCH"])
@check_user_auth
def patch_share_session_permissions(session_id: str):
    user_id = _current_user_id()
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        return jsonify({"error": "permissions_required"}), 400
    service = get_share_session_service()
    try:
        ok, reason, session_item = service.update_session_permissions(
            session_id=session_id,
            actor_user_id=user_id,
            permissions=permissions,
        )
    except PermissionContractError as exc:
        return jsonify({"error": exc.reason_code, "field": exc.field}), 400
    if not ok:
        if reason == "forbidden":
            return jsonify({"error": reason}), 403
        if reason == "session_not_found":
            return jsonify({"error": reason}), 404
        return jsonify({"error": reason or "update_failed"}), 400
    audit_permission_changed(
        session_id=session_id,
        actor_user_id=user_id,
        new_permissions=dict((session_item or {}).get("permissions") or {}),
    )
    return jsonify({"ok": True, "data": session_item}), 200


register_share_session_view_routes(share_sessions_bp)
register_share_session_chat_routes(share_sessions_bp)


@share_sessions_bp.route("/share-sessions/<session_id>/participants/<participant_id>", methods=["DELETE"])
@check_user_auth
def revoke_share_session_participant(session_id: str, participant_id: str):
    user_id = _current_user_id()
    service = get_share_session_service()
    ok, reason, participant = service.revoke_participant(
        session_id=session_id,
        participant_id=participant_id,
        actor_user_id=user_id,
    )
    if not ok:
        if reason == "forbidden":
            return jsonify({"error": reason}), 403
        if reason in {"session_not_found", "participant_not_found"}:
            return jsonify({"error": reason}), 404
        return jsonify({"error": reason or "revoke_failed"}), 400
    audit_participant_revoked(session_id=session_id, participant_id=participant_id, actor_user_id=user_id)
    return jsonify({"ok": True, "data": participant}), 200
