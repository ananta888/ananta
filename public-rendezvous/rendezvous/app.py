"""Ananta Public Rendezvous Service – standalone Flask-App.

Endpunkte:
  GET  /health
  GET  /info
  POST /rendezvous/sessions
  GET  /rendezvous/sessions
  POST /rendezvous/sessions/join
  POST /rendezvous/sessions/<id>/join
  GET  /rendezvous/sessions/<id>/participants
  PATCH /rendezvous/sessions/<id>/permissions
  DELETE /rendezvous/sessions/<id>
  GET  /rendezvous/turn-credentials
  POST /webrtc/sessions/<id>/signal
  GET  /webrtc/sessions/<id>/signal
  GET/POST /signaling          (HTTP-Polling-Alias für WebSocket-kompatible Clients)
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

from flask import Flask, jsonify, request

import config as cfg
import service as svc
from oidc_auth import AuthContext, verify_bearer_token

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


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


# --- Health / Info ---

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "ananta-rendezvous"}), 200


@app.get("/info")
def info():
    return jsonify({
        "service": "ananta-rendezvous",
        "oidc_issuer": cfg.OIDC_ISSUER,
        "turn_realm": cfg.TURN_REALM,
        "turn_urls": cfg.TURN_URLS,
        "session_max_minutes": cfg.SESSION_MAX_DURATION_SECONDS // 60,
    }), 200


# --- Rendezvous sessions ---

@app.post("/rendezvous/sessions")
def create_session():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    if not svc._rate_check("create", ctx.sub, cfg.RATE_CREATE_LIMIT, cfg.RATE_CREATE_WINDOW):
        return jsonify({"error": "rate_limited"}), 429
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    device_fp = str(body.get("owner_device_fingerprint") or "").strip()
    if not device_fp:
        return jsonify({"error": "owner_device_fingerprint_required"}), 400
    session = svc.create_session(
        owner_user_id=ctx.username,
        owner_user_sub=ctx.sub,
        owner_device_fingerprint=device_fp,
        oidc_issuer=cfg.OIDC_ISSUER,
        allowed_permissions=body.get("allowed_permissions"),
        title=str(body.get("title") or "Rendezvous Session"),
    )
    log.info("session_created id=%s owner=%s", session["id"], ctx.username)
    return jsonify({"ok": True, "data": session}), 201


@app.get("/rendezvous/sessions")
def list_sessions():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    sessions = svc.list_sessions_for_user(requester_user_id=ctx.username)
    return jsonify({"ok": True, "data": {"items": sessions}}), 200


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
    ip = str(request.remote_addr or "unknown")
    if not svc._rate_check("join_ip", ip, cfg.RATE_JOIN_LIMIT, cfg.RATE_JOIN_WINDOW):
        return jsonify({"error": "rate_limited"}), 429
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    invite_code = str(body.get("invite_code") or "").strip()
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    result = svc.join_session(
        invite_code=invite_code,
        user_id=ctx.username,
        user_sub=ctx.sub,
        device_id=str(body.get("device_id") or "").strip(),
        device_fingerprint=str(body.get("device_fingerprint") or "").strip(),
        oidc_issuer=cfg.OIDC_ISSUER,
        expected_session_id=expected_session_id,
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = 404 if reason == "session_not_found" else 403 if reason in {
            "session_revoked", "session_expired", "oidc_issuer_mismatch", "forbidden"
        } else 400
        return jsonify({"error": reason}), status
    session_label = expected_session_id or "invite"
    log.info("participant_joined session=%s user=%s", session_label, ctx.username)
    return jsonify({"ok": True, "data": result.get("participant")}), 201 if not result.get("idempotent") else 200


@app.get("/rendezvous/sessions/<session_id>/participants")
def list_participants(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.get_participants(session_id=session_id, requester_user_id=ctx.username)
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    svc.touch_participant(session_id=session_id, user_id=ctx.username)
    return jsonify({"ok": True, "data": {"participants": result["participants"]}}), 200


@app.patch("/rendezvous/sessions/<session_id>/permissions")
def update_permissions(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        return jsonify({"error": "permissions_required"}), 400
    result = svc.update_session_permissions(
        session_id=session_id,
        actor_user_id=ctx.username,
        permissions=permissions,
    )
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    return jsonify({"ok": True, "data": result.get("session")}), 200


@app.delete("/rendezvous/sessions/<session_id>")
def revoke_session(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.revoke_session(session_id=session_id, actor_user_id=ctx.username)
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), 403 if reason == "forbidden" else 404
    log.info("session_revoked id=%s actor=%s", session_id, ctx.username)
    return jsonify({"ok": True}), 200


@app.get("/rendezvous/turn-credentials")
def turn_credentials():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    creds = svc.issue_turn_credentials(ctx.username)
    if not creds:
        return jsonify({"error": "turn_not_configured"}), 503
    return jsonify({"ok": True, "data": creds}), 200


# --- WebRTC Signaling ---

@app.post("/webrtc/sessions/<session_id>/signal")
def push_signal(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    if not svc._rate_check("signal", ctx.sub, cfg.RATE_SIGNAL_LIMIT, cfg.RATE_SIGNAL_WINDOW):
        return jsonify({"error": "rate_limited"}), 429
    raw = request.get_data(as_text=False)
    if len(raw) > svc._MAX_SIGNAL_BYTES:
        return jsonify({"error": "signal_too_large"}), 413
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    recipient_id = str(body.get("recipient_id") or "").strip()
    if not recipient_id:
        return jsonify({"error": "recipient_id_required"}), 400
    signal_type = str(body.get("type") or "").strip()
    result = svc.push_signal(
        session_id=session_id,
        sender_id=ctx.username,
        recipient_id=recipient_id,
        signal_type=signal_type,
        payload=body.get("payload"),
    )
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), 403 if "forbidden" in reason else 400
    return jsonify({"ok": True, "signal_id": result["signal_id"]}), 201


@app.get("/webrtc/sessions/<session_id>/signal")
def poll_signals(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    if not svc.is_authorized_participant(session_id, ctx.username):
        return jsonify({"error": "forbidden"}), 403
    signals = svc.consume_signals(session_id=session_id, user_id=ctx.username)
    svc.touch_participant(session_id=session_id, user_id=ctx.username)
    return jsonify({"ok": True, "data": {"signals": signals}}), 200


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


@app.errorhandler(500)
def internal_error(exc):
    log.exception("Internal error: %s", exc)
    return jsonify({"error": "internal_error"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
