from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.rate_limit_service import RateLimitService
from agent.services.share_audit_service import (
    audit_chat_sent,
    audit_participant_joined,
    audit_participant_revoked,
    audit_permission_changed,
    audit_session_created,
    audit_view_delta_sent,
    audit_view_started,
)
from agent.services.share_session_service import get_share_session_service

share_sessions_bp = Blueprint("share_sessions", __name__)
_rate_limiter = RateLimitService()

_CHAT_QUEUE_MAX = 200
_CHAT_MSG_MAX_BYTES = 64 * 1024
_VIEW_QUEUE_MAX = 50
_VIEW_PAYLOAD_MAX_BYTES = 256 * 1024
_VIEW_FRAME_RATE = {"namespace": "share_view_push", "limit": 40, "window_seconds": 10}
_VIEW_POLL_RATE = {"namespace": "share_view_poll", "limit": 80, "window_seconds": 10}
_CHAT_SEND_RATE = {"namespace": "share_chat_send", "limit": 60, "window_seconds": 10}
_CHAT_POLL_RATE = {"namespace": "share_chat_poll", "limit": 120, "window_seconds": 10}

_view_queues: dict[str, list[dict[str, Any]]] = {}  # session_id -> frames
_chat_queues: dict[str, list[dict[str, Any]]] = {}  # session_id -> chat messages
_view_started_audited: set[str] = set()
_participant_last_seen: dict[str, float] = {}  # participant_id -> timestamp


def _is_session_active(session_item: dict[str, Any]) -> bool:
    if not isinstance(session_item, dict):
        return False
    if session_item.get("revoked_at") is not None:
        return False
    exp = session_item.get("expires_at")
    if isinstance(exp, (int, float)) and float(exp) <= time.time():
        return False
    return True


def _is_active_participant(*, session_id: str, user_id: str, session_item: dict[str, Any] | None = None) -> bool:
    if not user_id:
        return False
    service = get_share_session_service()
    session = session_item if isinstance(session_item, dict) else service.get_session(session_id)
    if not isinstance(session, dict) or not _is_session_active(session):
        return False
    if str(session.get("owner_user_id") or "") == user_id:
        return True
    participants = service.get_participants(session_id)
    return any(str(p.get("user_id") or "") == user_id and not p.get("revoked_at") for p in participants)

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
    owner_device_id = str(body.get("owner_device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
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
    service = get_share_session_service()
    session_item = service.get_session_by_invite_code(invite_code)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    session_id = str(session_item.get("id") or "")
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
    )
    if not joined.ok:
        code_map = {"session_not_found": 404, "invalid_invite": 403, "session_revoked": 403, "session_expired": 403}
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
        participants.insert(0, {
            "id": f"owner-{owner_id}",
            "user_id": owner_id,
            "device_id": str(session_item.get("owner_device_id") or ""),
            "role": "owner",
            "permissions": dict(session_item.get("permissions") or {}),
            "joined_at": float(session_item.get("created_at") or 0),
            "revoked_at": None,
            "last_seen_at": _participant_last_seen.get(f"owner-{owner_id}"),
        })
    return jsonify({"ok": True, "participants": participants}), 200


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
    _view_queues.pop(session_id, None)
    _chat_queues.pop(session_id, None)
    _view_started_audited.discard(session_id)
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
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
    )
    if not joined.ok:
        if joined.reason == "session_not_found":
            return jsonify({"error": joined.reason}), 404
        if joined.reason in {"invalid_invite", "session_revoked", "session_expired"}:
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
    audit_permission_changed(
        session_id=session_id,
        actor_user_id=user_id,
        new_permissions=dict((session_item or {}).get("permissions") or {}),
    )
    return jsonify({"ok": True, "data": session_item}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/view/push", methods=["POST"])
@check_user_auth
def push_view_payload(session_id: str):
    """SS05.04: Owner schickt verschlüsselten Snapshot/Delta an Hub."""
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if session_item.get("owner_user_id") != user_id:
        return jsonify({"error": "forbidden"}), 403
    if not _rate_limiter.allow_request(
        namespace=_VIEW_FRAME_RATE["namespace"],
        subject=user_id,
        limit=_VIEW_FRAME_RATE["limit"],
        window_seconds=_VIEW_FRAME_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429
    if not session_item.get("permissions", {}).get("view_tui"):
        return jsonify({"error": "view_tui_permission_required"}), 403
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if not body.get("encrypted_payload"):
        return jsonify({"error": "encrypted_payload_required"}), 400
    raw = request.get_data(as_text=False)
    if len(raw) > _VIEW_PAYLOAD_MAX_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    if session_id not in _view_started_audited:
        audit_view_started(session_id=session_id, owner_user_id=user_id)
        _view_started_audited.add(session_id)
    message_id = str(body.get("message_id") or str(uuid.uuid4()))
    entry: dict[str, Any] = {
        "session_id": session_id,
        "message_id": message_id,
        "kind": str(body.get("kind") or "snapshot"),
        "width": int(body.get("width") or 0),
        "height": int(body.get("height") or 0),
        "base_hash": str(body.get("base_hash") or ""),
        "new_hash": str(body.get("new_hash") or ""),
        "encrypted_payload": body.get("encrypted_payload"),
        "pushed_at": time.time(),
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
    audit_view_delta_sent(
    session_id=session_id,
    owner_user_id=user_id,
    kind=str(entry["kind"]),
    new_hash=str(entry["new_hash"]),
    policy_hash=str(entry["base_hash"] or entry["new_hash"] or ""),
    )
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
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _rate_limiter.allow_request(
        namespace=_VIEW_POLL_RATE["namespace"],
        subject=user_id,
        limit=_VIEW_POLL_RATE["limit"],
        window_seconds=_VIEW_POLL_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429
    perms = session_item.get("permissions") or {}
    if not perms.get("view_tui"):
        return jsonify({"error": "view_tui_permission_required"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    since = str(request.args.get("since") or "").strip()
    if since == "0":
        since = ""
    frames = list(_view_queues.get(session_id) or [])
    if since:
        # Gibt nur Frames zurück, die nach dem gegebenen message_id kommen
        for i, f in enumerate(frames):
            if f.get("message_id") == since:
                frames = frames[i + 1:]
                break
    # T06: Pair-Dev view-sync contract. The frontend expects
    # `view_messages` (a flat list of RelayEnvelopes) and a
    # `view_cursor` to advance through the queue. We keep the
    # legacy `data.frames` shape for backwards compatibility
    # with any older client still on it.
    view_messages = [
        {
            "message_id": f.get("message_id"),
            "kind": f.get("kind"),
            "base_hash": f.get("base_hash"),
            "new_hash": f.get("new_hash"),
            "width": f.get("width"),
            "height": f.get("height"),
            "encrypted_payload": f.get("encrypted_payload"),
        }
        for f in frames[-10:]
    ]
    last_id = view_messages[-1]["message_id"] if view_messages else since
    return jsonify({
        "ok": True,
        "view_messages": view_messages,
        "messages": view_messages,
        "payloads": view_messages,
        "view_cursor": last_id or "",
        "data": {"frames": frames[-10:]},
    }), 200


@share_sessions_bp.route("/share-sessions/<session_id>/chat/messages", methods=["POST"])
@check_user_auth
def send_share_chat_message(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active", "blocked": True}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant", "blocked": True}), 403
    if not bool((session_item.get("permissions") or {}).get("chat")):
        return jsonify({"error": "chat_permission_required", "blocked": True}), 403
    if not _rate_limiter.allow_request(
        namespace=_CHAT_SEND_RATE["namespace"],
        subject=user_id,
        limit=_CHAT_SEND_RATE["limit"],
        window_seconds=_CHAT_SEND_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited", "blocked": True}), 429

    raw = request.get_data(as_text=False)
    if len(raw) > _CHAT_MSG_MAX_BYTES:
        return jsonify({"error": "payload_too_large", "blocked": True}), 413
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_id = str(body.get("id") or str(uuid.uuid4()))
    encrypted_payload = body.get("encrypted_payload")
    text = str(body.get("text") or "")
    if not encrypted_payload and not text:
        return jsonify({"error": "message_required", "blocked": True}), 400
    message = {
        "id": message_id,
        "share_session_id": session_id,
        "from_id": str(body.get("from_id") or user_id),
        "channel_type": str(body.get("channel_type") or "room"),
        "visibility": str(body.get("visibility") or "room"),
        "encrypted_payload": encrypted_payload,
        "text": text,
        "created_at": time.time(),
    }
    queue = _chat_queues.setdefault(session_id, [])
    queue.append(message)
    _chat_queues[session_id] = queue[-_CHAT_QUEUE_MAX:]
    audit_chat_sent(
        session_id=session_id,
        sender_user_id=user_id,
        message_id=message_id,
        is_encrypted=bool(encrypted_payload),
    )
    return jsonify({"ok": True, "data": {"id": message_id}, "blocked": False}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/chat/messages", methods=["GET"])
@check_user_auth
def list_share_chat_messages(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _rate_limiter.allow_request(
        namespace=_CHAT_POLL_RATE["namespace"],
        subject=user_id,
        limit=_CHAT_POLL_RATE["limit"],
        window_seconds=_CHAT_POLL_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429

    messages = list(_chat_queues.get(session_id) or [])
    since = str(request.args.get("since") or "").strip()
    if since:
        for index, message in enumerate(messages):
            if str(message.get("id") or "") == since:
                messages = messages[index + 1 :]
                break
    messages = messages[-100:]
    cursor = str(messages[-1].get("id") or since) if messages else since
    return jsonify({"ok": True, "messages": messages, "cursor": cursor}), 200


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
    audit_participant_revoked(session_id=session_id, participant_id=participant_id, actor_user_id=user_id)
    return jsonify({"ok": True, "data": participant}), 200
