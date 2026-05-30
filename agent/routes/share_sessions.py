from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.share_session_service import get_share_session_service

share_sessions_bp = Blueprint("share_sessions", __name__)


def _current_user_id() -> str:
    auth = dict(get_request_auth_context() or {})
    return str(auth.get("sub") or auth.get("username") or "").strip()


def _current_device_id() -> str:
    raw = request.headers.get("X-Ananta-Device-Id")
    return str(raw or "").strip()


@share_sessions_bp.route("/share-sessions", methods=["POST"])
@check_user_auth
def create_share_session():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    owner_device_id = str(body.get("owner_device_id") or _current_device_id() or "").strip()
    if not owner_device_id:
        return jsonify({"error": "owner_device_id_required"}), 400
    service = get_share_session_service()
    session_item = service.create_session(
        owner_user_id=user_id,
        owner_device_id=owner_device_id,
        title=str(body.get("title") or "Shared Session").strip() or "Shared Session",
        mode=str(body.get("mode") or "relay").strip() or "relay",
        transport=str(body.get("transport") or "hub_relay").strip() or "hub_relay",
        permissions=dict(body.get("permissions") or {}),
        expires_at=float(body["expires_at"]) if isinstance(body.get("expires_at"), (int, float)) else None,
    )
    log_audit(
        "share_session_created",
        {
            "share_session_id": session_item.get("id"),
            "owner_user_id": user_id,
            "owner_device_id": owner_device_id,
            "mode": session_item.get("mode"),
            "transport": session_item.get("transport"),
            "permissions": dict(session_item.get("permissions") or {}),
        },
    )
    return jsonify({"ok": True, "data": session_item}), 201


@share_sessions_bp.route("/share-sessions", methods=["GET"])
@check_user_auth
def list_share_sessions():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    items = service.list_sessions_for_owner(user_id)
    return jsonify({"ok": True, "data": {"items": items}}), 200


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
    service = get_share_session_service()
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
    )
    if not joined.ok:
        if joined.reason in {"session_not_found"}:
            return jsonify({"error": joined.reason}), 404
        if joined.reason in {"invalid_invite", "session_revoked", "session_expired"}:
            return jsonify({"error": joined.reason}), 403
        return jsonify({"error": joined.reason or "join_failed"}), 400
    participant = dict(joined.participant or {})
    log_audit(
        "share_session_participant_joined",
        {
            "share_session_id": session_id,
            "participant_id": participant.get("id"),
            "user_id": user_id,
            "device_id": participant.get("device_id"),
            "public_key_fingerprint": participant.get("public_key_fingerprint"),
            "permissions": dict(participant.get("permissions") or {}),
        },
    )
    return jsonify({"ok": True, "data": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/permissions", methods=["PATCH"])
@check_user_auth
def patch_share_session_permissions(session_id: str):
    user_id = _current_user_id()
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        return jsonify({"error": "permissions_required"}), 400
    service = get_share_session_service()
    ok, reason, session_item = service.update_session_permissions(
        session_id=session_id,
        actor_user_id=user_id,
        permissions=permissions,
    )
    if not ok:
        if reason == "forbidden":
            return jsonify({"error": reason}), 403
        if reason == "session_not_found":
            return jsonify({"error": reason}), 404
        return jsonify({"error": reason or "update_failed"}), 400
    log_audit(
        "share_session_permission_changed",
        {
            "share_session_id": session_id,
            "actor_user_id": user_id,
            "permissions": dict((session_item or {}).get("permissions") or {}),
        },
    )
    return jsonify({"ok": True, "data": session_item}), 200


_view_queues: dict[str, list[dict]] = {}  # session_id -> list of view payloads
_VIEW_QUEUE_MAX = 50
_VIEW_PAYLOAD_MAX_BYTES = 256 * 1024


@share_sessions_bp.route("/share-sessions/<session_id>/view/push", methods=["POST"])
@check_user_auth
def push_view_payload(session_id: str):
    """SS05.04: Owner schickt verschlüsselten Snapshot/Delta an Hub."""
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not session_item:
        return jsonify({"error": "session_not_found"}), 404
    if session_item.get("owner_user_id") != user_id:
        return jsonify({"error": "forbidden"}), 403
    if not session_item.get("permissions", {}).get("view_tui"):
        return jsonify({"error": "view_tui_permission_required"}), 403
    body: dict = request.get_json(force=True, silent=True) or {}
    if not body.get("encrypted_payload"):
        return jsonify({"error": "encrypted_payload_required"}), 400
    raw = request.get_data(as_text=False)
    if len(raw) > _VIEW_PAYLOAD_MAX_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    import time as _time
    entry = {
        "session_id": session_id,
        "message_id": str(body.get("message_id") or ""),
        "kind": str(body.get("kind") or "snapshot"),
        "width": int(body.get("width") or 0),
        "height": int(body.get("height") or 0),
        "base_hash": str(body.get("base_hash") or ""),
        "new_hash": str(body.get("new_hash") or ""),
        "encrypted_payload": body.get("encrypted_payload"),
        "pushed_at": _time.time(),
    }
    if session_id not in _view_queues:
        _view_queues[session_id] = []
    _view_queues[session_id].append(entry)
    _view_queues[session_id] = _view_queues[session_id][-_VIEW_QUEUE_MAX:]
    log_audit("share_session_view_delta_sent", {
        "share_session_id": session_id,
        "owner_user_id": user_id,
        "kind": entry["kind"],
        "new_hash": entry["new_hash"],
    })
    return jsonify({"ok": True}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/view/poll", methods=["GET"])
@check_user_auth
def poll_view_payload(session_id: str):
    """SS05.04: Teilnehmer holt verschlüsselte Snapshots/Deltas ab."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not session_item:
        return jsonify({"error": "session_not_found"}), 404
    perms = session_item.get("permissions") or {}
    if not perms.get("view_tui"):
        return jsonify({"error": "view_tui_permission_required"}), 403
    # Prüfe ob User Teilnehmer ist
    participants = service.get_participants(session_id)
    is_participant = any(
        p.get("user_id") == user_id and not p.get("revoked_at")
        for p in participants
    ) or session_item.get("owner_user_id") == user_id
    if not is_participant:
        return jsonify({"error": "not_a_participant"}), 403
    since = str(request.args.get("since") or "").strip()
    frames = list(_view_queues.get(session_id) or [])
    if since:
        # Gibt nur Frames zurück, die nach dem gegebenen message_id kommen
        for i, f in enumerate(frames):
            if f.get("message_id") == since:
                frames = frames[i + 1:]
                break
    return jsonify({"ok": True, "data": {"frames": frames[-10:]}}), 200


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
    log_audit(
        "share_session_participant_revoked",
        {
            "share_session_id": session_id,
            "participant_id": participant_id,
            "actor_user_id": user_id,
        },
    )
    return jsonify({"ok": True, "data": participant}), 200
