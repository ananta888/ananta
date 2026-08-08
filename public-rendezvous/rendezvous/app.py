"""Ananta Public Rendezvous Service – standalone Flask-App.

Endpunkte:
  GET  /health
  GET  /info
  POST /rendezvous/sessions
  GET  /rendezvous/sessions
  POST /rendezvous/sessions/join
  POST /rendezvous/sessions/<id>/join
  GET  /rendezvous/sessions/<id>/participants
  GET  /rendezvous/sessions/<id>/security/key-packages
  GET/POST /rendezvous/sessions/<id>/security/key-confirmations
  PATCH /rendezvous/sessions/<id>/permissions
  DELETE /rendezvous/sessions/<id>
  GET  /rendezvous/turn-credentials?session_id=<id>
  POST /webrtc/sessions/<id>/signal
  GET  /webrtc/sessions/<id>/signal
  GET/POST /signaling          (HTTP-Polling-Alias für WebSocket-kompatible Clients)
"""

from __future__ import annotations

import logging
import math
import os
import sys
import uuid
from typing import Any

import service as svc
from flask import Flask, jsonify, request
from oidc_auth import AuthContext, verify_bearer_token

import config as cfg

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

_TURN_CREDENTIAL_ERROR_STATUS = {
    "session_not_found": 404,
    "forbidden": 403,
    "turn_not_configured": 503,
}


@app.after_request
def add_cors_headers(response):
    """Allow only explicitly configured browser app origins."""
    origin = str(request.headers.get("Origin") or "").rstrip("/")
    if origin and origin in cfg.CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


# --- Auth helper ---


def _require_auth() -> AuthContext | None:
    """Gibt AuthContext zurück oder schreibt 401/403-Response und gibt None zurück."""
    auth_header = request.headers.get("Authorization", "")
    try:
        return verify_bearer_token(auth_header)
    except ValueError as exc:
        log.debug("Auth failed: %s", exc)
        return None


def _auth_error(msg: str = "unauthorized", status: int = 401):
    return jsonify({"error": msg}), status


def _closed_json_body(allowed_fields: set[str]):
    """Parse a JSON object and reject fields outside the endpoint contract."""
    body = request.get_json(force=False, silent=True)
    if not isinstance(body, dict):
        return None, (jsonify({"error": "json_object_required"}), 400)
    if any(not isinstance(key, str) or key not in allowed_fields for key in body):
        return None, (jsonify({"error": "request_fields_not_allowed"}), 400)
    return body, None


def _session_for_local_peer(session: Any, peer_id: str) -> Any:
    if not isinstance(session, dict):
        return session
    return {**session, "local_peer_id": peer_id}


# --- Health / Info ---


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "ananta-rendezvous"}), 200


@app.get("/info")
def info():
    return jsonify(
        {
            "service": "ananta-rendezvous",
            "oidc_issuer": cfg.OIDC_ISSUER,
            "turn_realm": cfg.TURN_REALM,
            "turn_urls": cfg.TURN_URLS,
            "session_max_minutes": cfg.SESSION_MAX_DURATION_SECONDS // 60,
        }
    ), 200


# --- Rendezvous sessions ---


@app.post("/rendezvous/sessions")
def create_session():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    if not svc._rate_check("create", ctx.peer_id, cfg.RATE_CREATE_LIMIT, cfg.RATE_CREATE_WINDOW):
        return jsonify({"error": "rate_limited"}), 429
    body, body_error = _closed_json_body(
        {
            "title",
            "permissions",
            "allowed_permissions",
            "permissions_version",
            "security_contract_version",
            "security_mode",
            "public_key_spki_b64",
            "public_key_fingerprint",
            "mode",
            "transport",
            "expires_at",
            "owner_device_id",
            "owner_device_fingerprint",
        }
    )
    if body_error:
        return body_error
    assert body is not None
    if (
        body.get("security_mode") not in {None, "strict_e2ee"}
        or body.get("security_contract_version") not in {None, 1}
        or body.get("mode") not in {None, "p2p"}
        or body.get("transport") not in {None, "webrtc"}
    ):
        return jsonify({"error": "strict_e2ee_required"}), 400
    device_fp = str(body.get("owner_device_fingerprint") or "").strip()
    device_id = str(body.get("owner_device_id") or "").strip()
    public_key = str(body.get("public_key_spki_b64") or "").strip()
    if not device_id or not device_fp or not public_key:
        return jsonify({"error": "device_identity_required"}), 400
    requested_expires_at = body.get("expires_at")
    if requested_expires_at is not None and (
        isinstance(requested_expires_at, bool)
        or not isinstance(requested_expires_at, (int, float))
        or not math.isfinite(float(requested_expires_at))
    ):
        return jsonify({"error": "session_expiry_invalid"}), 400
    try:
        session = svc.create_session(
            owner_user_id=ctx.peer_id,
            owner_user_sub=ctx.sub,
            owner_device_fingerprint=device_fp,
            owner_device_id=device_id,
            owner_public_key_spki_b64=public_key,
            oidc_issuer=ctx.issuer,
            allowed_permissions=body.get("allowed_permissions") or body.get("permissions"),
            title=str(body.get("title") or "Rendezvous Session"),
            requested_expires_at=requested_expires_at,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    local_session = _session_for_local_peer(session, ctx.peer_id)
    log.info("session_created id=%s owner_peer=%s", session["id"], ctx.peer_id)
    return jsonify(
        {
            "ok": True,
            "local_peer_id": ctx.peer_id,
            "session": local_session,
            "data": local_session,
        }
    ), 201


@app.get("/rendezvous/sessions")
def list_sessions():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    sessions = [
        _session_for_local_peer(session, ctx.peer_id)
        for session in svc.list_sessions_for_user(requester_user_id=ctx.peer_id)
    ]
    return jsonify(
        {
            "ok": True,
            "local_peer_id": ctx.peer_id,
            "data": {"items": sessions, "local_peer_id": ctx.peer_id},
        }
    ), 200


@app.post("/rendezvous/sessions/join")
def join_session_by_invite():
    return _join_session_by_invite(expected_session_id="")


@app.post("/rendezvous/sessions/<session_id>/join")
def join_session(session_id: str):
    return _join_session_by_invite(expected_session_id=session_id)


def _join_session_by_invite(*, expected_session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    # Join is OIDC-authenticated before rate limiting. The canonical peer ID
    # avoids a shared Caddy bridge-IP bucket and cannot be changed through an
    # untrusted X-Forwarded-For header.
    if not svc._rate_check("join_peer", ctx.peer_id, cfg.RATE_JOIN_LIMIT, cfg.RATE_JOIN_WINDOW):
        return jsonify({"error": "rate_limited"}), 429
    body, body_error = _closed_json_body(
        {
            "invite_code",
            "minimum_security_mode",
            "public_key_spki_b64",
            "public_key_fingerprint",
            "device_id",
            "device_fingerprint",
        }
    )
    if body_error:
        return body_error
    assert body is not None
    if body.get("minimum_security_mode") not in {None, "strict_e2ee"}:
        return jsonify({"error": "strict_e2ee_required"}), 400
    invite_code = str(body.get("invite_code") or "").strip()
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    result = svc.join_session(
        invite_code=invite_code,
        user_id=ctx.peer_id,
        user_sub=ctx.sub,
        device_id=str(body.get("device_id") or "").strip(),
        device_fingerprint=str(body.get("device_fingerprint") or "").strip(),
        public_key_spki_b64=str(body.get("public_key_spki_b64") or "").strip(),
        oidc_issuer=ctx.issuer,
        expected_session_id=expected_session_id,
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = (
            404
            if reason == "session_not_found"
            else 403
            if reason in {"session_revoked", "session_expired", "oidc_issuer_mismatch", "forbidden"}
            else 400
        )
        return jsonify({"error": reason}), status
    session_label = expected_session_id or "invite"
    local_session = _session_for_local_peer(result.get("session"), ctx.peer_id)
    log.info("participant_joined session=%s peer=%s", session_label, ctx.peer_id)
    return jsonify(
        {
            "ok": True,
            "local_peer_id": ctx.peer_id,
            "participant": result.get("participant"),
            "session": local_session,
            "data": local_session,
        }
    ), 201 if not result.get("idempotent") else 200


@app.get("/rendezvous/sessions/<session_id>/participants")
def list_participants(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.get_participants(session_id=session_id, requester_user_id=ctx.peer_id)
    if not result.get("ok"):
        reason = result["reason"]
        status = 403 if reason == "forbidden" else 404 if reason == "session_not_found" else 409
        return jsonify({"error": reason}), status
    svc.touch_participant(session_id=session_id, user_id=ctx.peer_id)
    return jsonify(
        {
            "ok": True,
            "local_peer_id": ctx.peer_id,
            "data": {"participants": result["participants"], "local_peer_id": ctx.peer_id},
        }
    ), 200


@app.get("/rendezvous/sessions/<session_id>/security/key-packages")
def key_packages(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.get_key_packages(session_id=session_id, requester_user_id=ctx.peer_id)
    if not result.get("ok"):
        reason = result["reason"]
        status = 404 if reason == "session_not_found" else 403 if reason == "forbidden" else 409
        return jsonify({"error": reason}), status
    return jsonify({**result, "local_peer_id": ctx.peer_id}), 200


@app.post("/rendezvous/sessions/<session_id>/security/key-confirmations")
def put_key_confirmation(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    body, body_error = _closed_json_body(
        {
            "recipient_peer_id",
            "package_id",
            "epoch",
            "confirmation_tag",
        }
    )
    if body_error:
        return body_error
    assert body is not None
    epoch = body.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        return jsonify({"error": "epoch_invalid"}), 400
    result = svc.put_key_confirmation(
        session_id=session_id,
        sender_peer_id=ctx.peer_id,
        recipient_peer_id=str(body.get("recipient_peer_id") or "").strip(),
        package_id=str(body.get("package_id") or "").strip(),
        epoch=epoch,
        confirmation_tag=str(body.get("confirmation_tag") or "").strip(),
    )
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 409
    return jsonify({**result, "local_peer_id": ctx.peer_id}), 200 if result.get("idempotent") else 201


@app.get("/rendezvous/sessions/<session_id>/security/key-confirmations")
def get_key_confirmation(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    sender_peer_id = str(request.args.get("sender_peer_id") or "").strip()
    result = svc.get_key_confirmation(
        session_id=session_id,
        requester_user_id=ctx.peer_id,
        sender_peer_id=sender_peer_id,
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = 403 if reason == "forbidden" else 404 if reason == "session_not_found" else 409
        return jsonify({"error": reason}), status
    return jsonify({**result, "local_peer_id": ctx.peer_id}), 200


@app.patch("/rendezvous/sessions/<session_id>/permissions")
def update_permissions(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    body, body_error = _closed_json_body({"permissions"})
    if body_error:
        return body_error
    assert body is not None
    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        return jsonify({"error": "permissions_required"}), 400
    result = svc.update_session_permissions(
        session_id=session_id,
        actor_user_id=ctx.peer_id,
        permissions=permissions,
    )
    if not result.get("ok"):
        reason = result["reason"]
        if reason == "permission_update_rekey_required":
            return jsonify({"error": reason, "reason_code": reason}), 409
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    local_session = _session_for_local_peer(result.get("session"), ctx.peer_id)
    return jsonify(
        {
            "ok": True,
            "local_peer_id": ctx.peer_id,
            "data": local_session,
        }
    ), 200


@app.delete("/rendezvous/sessions/<session_id>")
def revoke_session(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.revoke_session(session_id=session_id, actor_user_id=ctx.peer_id)
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    log.info("session_revoked id=%s actor_peer=%s", session_id, ctx.peer_id)
    return jsonify({"ok": True, "local_peer_id": ctx.peer_id}), 200


@app.get("/rendezvous/turn-credentials")
def turn_credentials():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    if set(request.args) != {"session_id"} or len(request.args.getlist("session_id")) != 1:
        error = "session_id_required" if "session_id" not in request.args else "turn_request_invalid"
        return jsonify({"error": error}), 400
    raw_session_id = str(request.args.get("session_id") or "").strip()
    try:
        session_id = str(uuid.UUID(raw_session_id))
    except (AttributeError, ValueError):
        return jsonify({"error": "session_id_invalid"}), 400
    if not svc._rate_check(
        "turn_credentials",
        ctx.peer_id,
        cfg.RATE_TURN_CREDENTIAL_LIMIT,
        cfg.RATE_TURN_CREDENTIAL_WINDOW,
    ):
        return jsonify({"error": "rate_limited"}), 429
    result = svc.issue_turn_credentials(
        session_id=session_id,
        requester_user_id=ctx.peer_id,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "turn_credentials_unavailable")
        status = _TURN_CREDENTIAL_ERROR_STATUS.get(reason, 409)
        return jsonify({"error": reason}), status
    response = jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "local_peer_id": ctx.peer_id,
            "data": result["credentials"],
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 200


# --- WebRTC Signaling ---


@app.post("/webrtc/sessions/<session_id>/signal")
def push_signal(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    if not svc._rate_check("signal", ctx.peer_id, cfg.RATE_SIGNAL_LIMIT, cfg.RATE_SIGNAL_WINDOW):
        return jsonify({"error": "rate_limited"}), 429
    raw = request.get_data(as_text=False)
    if len(raw) > svc._MAX_SIGNAL_BYTES:
        return jsonify({"error": "signal_too_large"}), 413
    body, body_error = _closed_json_body(
        {
            "type",
            "session_id",
            "sender_id",
            "recipient_id",
            "payload",
        }
    )
    if body_error:
        return body_error
    assert body is not None
    declared_session = str(body.get("session_id") or "").strip()
    declared_sender = str(body.get("sender_id") or "").strip()
    if declared_session and declared_session != session_id:
        return jsonify({"error": "signal_session_mismatch"}), 400
    if declared_sender and declared_sender != ctx.peer_id:
        return jsonify({"error": "signal_sender_mismatch"}), 403
    recipient_id = str(body.get("recipient_id") or "").strip()
    if not recipient_id:
        return jsonify({"error": "recipient_id_required"}), 400
    signal_type = str(body.get("type") or "").strip()
    result = svc.push_signal(
        session_id=session_id,
        sender_id=ctx.peer_id,
        recipient_id=recipient_id,
        signal_type=signal_type,
        payload=body.get("payload"),
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = 403 if "forbidden" in reason else 400 if reason.startswith("invalid_signal") else 409
        return jsonify({"error": reason}), status
    return jsonify({**result, "local_peer_id": ctx.peer_id}), 201


@app.get("/webrtc/sessions/<session_id>/signal")
def poll_signals(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    since_values = request.args.getlist("since")
    if len(since_values) > 1:
        return jsonify({"error": "signal_cursor_invalid"}), 400
    raw_since = str(since_values[0]) if since_values else ""
    if raw_since and (len(raw_since) > 19 or not raw_since.isascii() or not raw_since.isdecimal()):
        return jsonify({"error": "signal_cursor_invalid"}), 400
    since = int(raw_since) if raw_since else 0
    if since > svc._MAX_SIGNAL_CURSOR:
        return jsonify({"error": "signal_cursor_invalid"}), 400
    if not svc._rate_check(
        "signal_poll",
        ctx.peer_id,
        cfg.RATE_SIGNAL_POLL_LIMIT,
        cfg.RATE_SIGNAL_POLL_WINDOW,
    ):
        return jsonify({"error": "rate_limited"}), 429
    result = svc.poll_signals(session_id=session_id, user_id=ctx.peer_id, since=since)
    if not result.get("ok"):
        reason = str(result.get("reason") or "forbidden")
        status = 400 if reason == "signal_cursor_invalid" else 403 if reason == "forbidden" else 409
        return jsonify({"error": reason}), status
    data = {key: value for key, value in result.items() if key != "ok"}
    return jsonify(
        {
            "ok": True,
            "local_peer_id": ctx.peer_id,
            "data": data,
        }
    ), 200


# --- /signaling Alias (HTTP-Polling, zukünftig WebSocket) ---


@app.route("/signaling", methods=["GET", "POST"])
def signaling_alias():
    """HTTP-Polling-Kompatibilitäts-Endpunkt. Leitet zu /webrtc/sessions/<id>/signal."""
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    session_id = str(request.args.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "session_id query param required"}), 400
    if request.method == "POST":
        return push_signal(session_id)
    return poll_signals(session_id)


# --- Error handlers ---


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "not_found"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "method_not_allowed"}), 405


@app.errorhandler(413)
def request_too_large(_):
    return jsonify({"error": "request_too_large"}), 413


@app.errorhandler(500)
def internal_error(exc):
    log.exception("Internal error: %s", exc)
    return jsonify({"error": "internal_error"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
