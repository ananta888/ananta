"""PRD03.02: Rendezvous API für Teilnehmer-Findung über webrtc.ananta.de."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.rate_limit_service import RateLimitService
from agent.services.rendezvous_service import get_rendezvous_service

rendezvous_bp = Blueprint("rendezvous", __name__)

_rate_limiter = RateLimitService()

_RATE_JOIN = {"namespace": "rendezvous_join", "limit": 10, "window_seconds": 60}
_RATE_CREATE = {"namespace": "rendezvous_create", "limit": 5, "window_seconds": 60}


def _current_auth() -> dict[str, str]:
    return dict(get_request_auth_context() or {})


def _require_oidc_sub() -> tuple[str, str] | None:
    """Gibt (user_id, oidc_sub) zurück. None wenn kein gültiger OIDC-Kontext."""
    auth = _current_auth()
    sub = str(auth.get("sub") or "").strip()
    user_id = str(auth.get("username") or sub).strip()
    if not sub or not user_id:
        return None
    return user_id, sub


@rendezvous_bp.route("/rendezvous/sessions", methods=["GET"])
@check_user_auth
def list_rendezvous_sessions():
    creds = _require_oidc_sub()
    if not creds:
        return jsonify({"error": "oidc_sub_required"}), 403
    user_id, _ = creds
    service = get_rendezvous_service()
    sessions = service.list_sessions_for_user(requester_user_id=user_id)
    return jsonify({"ok": True, "data": {"items": sessions}}), 200


@rendezvous_bp.route("/rendezvous/sessions", methods=["POST"])
@check_user_auth
def create_rendezvous_session():
    creds = _require_oidc_sub()
    if not creds:
        return jsonify({"error": "oidc_sub_required"}), 403
    user_id, sub = creds
    if not _rate_limiter.allow_request(namespace=_RATE_CREATE["namespace"], subject=user_id, limit=_RATE_CREATE["limit"], window_seconds=_RATE_CREATE["window_seconds"]):
        return jsonify({"error": "rate_limited"}), 429
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    device_fingerprint = str(body.get("owner_device_fingerprint") or "").strip()
    if not device_fingerprint:
        return jsonify({"error": "owner_device_fingerprint_required"}), 400
    oidc_issuer = str(body.get("oidc_issuer") or "").strip()
    service = get_rendezvous_service()
    session = service.create_session(
        owner_user_id=user_id,
        owner_device_fingerprint=device_fingerprint,
        oidc_issuer=oidc_issuer,
        allowed_permissions=body.get("allowed_permissions"),
        title=str(body.get("title") or "Rendezvous Session"),
    )
    log_audit("rendezvous_session_created", {"session_id": session.get("id"), "owner_user_id": user_id, "oidc_issuer": oidc_issuer})
    return jsonify({"ok": True, "data": session}), 201


@rendezvous_bp.route("/rendezvous/sessions/<session_id>/join", methods=["POST"])
@check_user_auth
def join_rendezvous_session(session_id: str):
    auth = _current_auth()
    sub = str(auth.get("sub") or "").strip()
    if not sub:
        return jsonify({"error": "oidc_sub_required"}), 403
    user_id = str(auth.get("username") or sub).strip()
    ip = str(request.remote_addr or "unknown")
    if not _rate_limiter.allow_request(namespace=_RATE_JOIN["namespace"], subject=ip, limit=_RATE_JOIN["limit"], window_seconds=_RATE_JOIN["window_seconds"]):
        return jsonify({"error": "rate_limited"}), 429
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    invite_code = str(body.get("invite_code") or "").strip()
    device_id = str(body.get("device_id") or "").strip()
    device_fingerprint = str(body.get("device_fingerprint") or "").strip()
    oidc_issuer = str(body.get("oidc_issuer") or "").strip()
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    service = get_rendezvous_service()
    result = service.join_session(
        invite_code=invite_code,
        user_id=user_id,
        user_sub=sub,
        device_id=device_id,
        device_fingerprint=device_fingerprint,
        oidc_issuer=oidc_issuer,
    )
    if not result.get("ok"):
        reason = result.get("reason", "join_failed")
        status = 404 if reason == "session_not_found" else 403 if reason in {"session_revoked", "session_expired", "oidc_issuer_mismatch", "forbidden"} else 400
        return jsonify({"error": reason}), status
    log_audit("rendezvous_participant_joined", {
        "session_id": session_id,
        "user_sub_hash": _hash_sub(sub),
        "device_fingerprint": device_fingerprint,
    })
    return jsonify({"ok": True, "data": result.get("participant")}), 201 if not result.get("idempotent") else 200


@rendezvous_bp.route("/rendezvous/sessions/<session_id>/participants", methods=["GET"])
@check_user_auth
def list_rendezvous_participants(session_id: str):
    creds = _require_oidc_sub()
    if not creds:
        return jsonify({"error": "oidc_sub_required"}), 403
    user_id, _ = creds
    service = get_rendezvous_service()
    result = service.get_participants(session_id=session_id, requester_user_id=user_id)
    if not result.get("ok"):
        reason = result.get("reason", "forbidden")
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    service.touch_participant(session_id=session_id, user_id=user_id)
    return jsonify({"ok": True, "data": {"participants": result.get("participants")}}), 200


@rendezvous_bp.route("/rendezvous/sessions/<session_id>", methods=["DELETE"])
@check_user_auth
def revoke_rendezvous_session(session_id: str):
    creds = _require_oidc_sub()
    if not creds:
        return jsonify({"error": "oidc_sub_required"}), 403
    user_id, sub = creds
    service = get_rendezvous_service()
    result = service.revoke_session(session_id=session_id, actor_user_id=user_id)
    if not result.get("ok"):
        reason = result.get("reason", "revoke_failed")
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    log_audit("rendezvous_session_revoked", {"session_id": session_id, "actor_user_sub_hash": _hash_sub(sub)})
    return jsonify({"ok": True}), 200


def _hash_sub(sub: str) -> str:
    import hashlib
    return hashlib.sha256(sub.encode()).hexdigest()[:16]
