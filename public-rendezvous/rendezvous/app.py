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
from flask import Flask, Response, jsonify, request
from oidc_auth import AuthContext, verify_bearer_token
from pair_security import SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS

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
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Ananta-Peer-Id, X-Ananta-Device-Id, X-Ananta-Membership-Capability"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Expose-Headers"] = "Retry-After"
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
    return {
        **{key: value for key, value in session.items() if not key.startswith("_")},
        "local_peer_id": peer_id,
    }


def _requested_peer_id() -> str:
    return str(request.headers.get("X-Ananta-Peer-Id") or "").strip()


def _requested_device_id() -> str:
    return str(request.headers.get("X-Ananta-Device-Id") or "").strip()


def _membership_capability() -> str:
    return str(request.headers.get("X-Ananta-Membership-Capability") or "").strip()


def _selected_peer_id(account_id: str) -> str:
    """Select a claimed peer; domain services authenticate it before use."""
    return _requested_peer_id() or account_id


def _rate_limit_guard(
    namespace: str,
    subject: str,
    limit: int,
    window: int,
) -> tuple[Response, int] | None:
    """Return a standards-compatible 429 response, or ``None`` when allowed."""
    allowed, retry_after = svc._rate_check_with_retry(namespace, subject, limit, window)
    if allowed:
        return None
    response = jsonify({"error": "rate_limited"})
    response.headers["Retry-After"] = str(retry_after)
    response.headers["Cache-Control"] = "no-store"
    return response, 429


def _recovery_probe_limit(account_id: str) -> tuple[Response, int] | None:
    """Bound idempotency lookups to the authenticated account."""
    return _rate_limit_guard(
        "recovery_probe",
        account_id,
        cfg.RATE_RECOVERY_PROBE_LIMIT,
        cfg.RATE_RECOVERY_PROBE_WINDOW,
    )


def _membership_probe_limit(account_id: str) -> tuple[Response, int] | None:
    """Bound membership resolution before trusting a client peer selector."""
    return _rate_limit_guard(
        "membership_probe",
        account_id,
        cfg.RATE_MEMBERSHIP_PROBE_LIMIT,
        cfg.RATE_MEMBERSHIP_PROBE_WINDOW,
    )


def _member_error_status(reason: str, *, default: int = 409) -> int:
    if reason in {
        "forbidden",
        "local_peer_id_required",
        "membership_capability_required",
        "membership_capability_invalid",
    }:
        return 403
    if reason == "session_not_found":
        return 404
    return default


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
            "supported_identity_binding_versions": [1, 2],
            "supported_public_media_e2ee_versions": list(SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS),
        }
    ), 200


# --- Rendezvous sessions ---


@app.post("/rendezvous/sessions")
def create_session():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
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
            "identity_binding_version",
            "public_media_e2ee_version",
            "public_media_capabilities",
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
    identity_binding_version = body.get("identity_binding_version", 1)
    if (
        isinstance(identity_binding_version, bool)
        or not isinstance(identity_binding_version, int)
        or identity_binding_version not in {1, 2}
    ):
        return jsonify({"error": "identity_binding_version_unsupported"}), 400
    try:
        public_media_e2ee_version = svc.normalize_public_media_advertisement(
            body.get("public_media_e2ee_version"),
            body.get("public_media_capabilities"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if public_media_e2ee_version and identity_binding_version != 2:
        return jsonify({"error": "public_media_identity_binding_v2_required"}), 400
    device_fp = str(body.get("owner_device_fingerprint") or "").strip()
    device_id = str(body.get("owner_device_id") or "").strip()
    public_key = str(body.get("public_key_spki_b64") or "").strip()
    if not device_id or not device_fp or not public_key:
        return jsonify({"error": "device_identity_required"}), 400
    membership_capability = _membership_capability()
    is_recovery = False
    if identity_binding_version == 2:
        if limited := _recovery_probe_limit(ctx.account_id):
            return limited
        is_recovery = svc.is_owner_create_recovery(
            account_id=ctx.account_id,
            device_fingerprint=device_fp,
            membership_capability=membership_capability,
            public_media_e2ee_version=public_media_e2ee_version,
        )
    if not is_recovery:
        if limited := _rate_limit_guard(
            "create", ctx.account_id, cfg.RATE_CREATE_LIMIT, cfg.RATE_CREATE_WINDOW,
        ):
            return limited
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
            identity_binding_version=identity_binding_version,
            membership_capability=membership_capability,
            public_media_e2ee_version=public_media_e2ee_version,
            public_media_capabilities=body.get("public_media_capabilities"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    idempotent = bool(session.pop("_idempotent", False))
    local_peer_id = str(session.get("owner_peer_id") or "") if identity_binding_version == 2 else ctx.account_id
    local_session = _session_for_local_peer(session, local_peer_id)
    log.info("session_created id=%s identity_binding_version=%d", session["id"], identity_binding_version)
    response = jsonify(
        {
            "ok": True,
            "local_peer_id": local_peer_id,
            "session": local_session,
            "data": local_session,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if idempotent else 201


@app.get("/rendezvous/sessions")
def list_sessions():
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    requested_peer_id = _requested_peer_id()
    sessions = svc.list_sessions_for_user(
        requester_user_id=ctx.account_id,
        requested_peer_id=requested_peer_id,
        requested_device_id=_requested_device_id(),
    )
    if not requested_peer_id and not _requested_device_id():
        sessions = [session for session in sessions if int(session.get("identity_binding_version") or 0) == 1]
    # Backward-compatible page-level v1 identity. V2 clients must use each
    # item's exact selector-bound local_peer_id instead.
    local_peer_id = requested_peer_id or ctx.account_id
    return jsonify(
        {
            "ok": True,
            "local_peer_id": local_peer_id,
            "data": {"items": sessions, "local_peer_id": local_peer_id},
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
    body, body_error = _closed_json_body(
        {
            "invite_code",
            "minimum_security_mode",
            "public_key_spki_b64",
            "public_key_fingerprint",
            "device_id",
            "device_fingerprint",
            "identity_binding_version",
            "public_media_e2ee_version",
            "public_media_capabilities",
        }
    )
    if body_error:
        return body_error
    assert body is not None
    if body.get("minimum_security_mode") not in {None, "strict_e2ee"}:
        return jsonify({"error": "strict_e2ee_required"}), 400
    expected_identity_binding_version = body.get("identity_binding_version")
    if expected_identity_binding_version is not None and (
        isinstance(expected_identity_binding_version, bool)
        or not isinstance(expected_identity_binding_version, int)
        or expected_identity_binding_version not in {1, 2}
    ):
        return jsonify({"error": "identity_binding_version_unsupported"}), 400
    try:
        public_media_e2ee_version = svc.normalize_public_media_advertisement(
            body.get("public_media_e2ee_version"),
            body.get("public_media_capabilities"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if public_media_e2ee_version and expected_identity_binding_version != 2:
        return jsonify({"error": "public_media_identity_binding_v2_required"}), 400
    invite_code = str(body.get("invite_code") or "").strip()
    membership_capability = _membership_capability()
    is_recovery = False
    if expected_identity_binding_version == 2:
        if limited := _recovery_probe_limit(ctx.account_id):
            return limited
        is_recovery = svc.is_join_recovery(
            invite_code=invite_code,
            account_id=ctx.account_id,
            device_fingerprint=str(body.get("device_fingerprint") or "").strip(),
            membership_capability=membership_capability,
            public_media_e2ee_version=public_media_e2ee_version,
        )
    # OIDC account identity, never forwarding headers, owns the abuse bucket.
    if not is_recovery:
        if limited := _rate_limit_guard(
            "join_peer", ctx.account_id, cfg.RATE_JOIN_LIMIT, cfg.RATE_JOIN_WINDOW,
        ):
            return limited
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
        membership_capability=membership_capability,
        expected_identity_binding_version=expected_identity_binding_version,
        public_media_e2ee_version=public_media_e2ee_version,
        public_media_capabilities=body.get("public_media_capabilities"),
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
    participant = result.get("participant") or {}
    local_peer_id = str(participant.get("peer_id") or participant.get("user_id") or "")
    local_session = _session_for_local_peer(result.get("session"), local_peer_id)
    log.info(
        "participant_joined session=%s identity_binding_version=%s",
        session_label,
        local_session.get("identity_binding_version"),
    )
    response = jsonify(
        {
            "ok": True,
            "local_peer_id": local_peer_id,
            "participant": participant,
            "session": local_session,
            "data": local_session,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 201 if not result.get("idempotent") else 200


@app.get("/rendezvous/sessions/<session_id>/participants")
def list_participants(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    requested_peer_id = _requested_peer_id()
    capability = _membership_capability()
    result = svc.get_participants(
        session_id=session_id,
        requester_user_id=ctx.account_id,
        requester_peer_id=requested_peer_id,
        membership_capability=capability,
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = _member_error_status(reason)
        return jsonify({"error": reason}), status
    touched = svc.touch_participant(
        session_id=session_id,
        user_id=ctx.account_id,
        requester_peer_id=requested_peer_id,
        membership_capability=capability,
    )
    if not touched.get("ok"):
        reason = str(touched.get("reason") or "forbidden")
        return jsonify({"error": reason}), _member_error_status(reason)
    local_peer_id = str(result["local_peer_id"])
    return jsonify(
        {
            "ok": True,
            "local_peer_id": local_peer_id,
            "data": {"participants": result["participants"], "local_peer_id": local_peer_id},
        }
    ), 200


@app.get("/rendezvous/sessions/<session_id>/security/key-packages")
def key_packages(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.get_key_packages(
        session_id=session_id,
        requester_user_id=ctx.account_id,
        requester_peer_id=_requested_peer_id(),
        membership_capability=_membership_capability(),
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = _member_error_status(reason)
        return jsonify({"error": reason}), status
    return jsonify(result), 200


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
        sender_peer_id=_selected_peer_id(ctx.account_id),
        recipient_peer_id=str(body.get("recipient_peer_id") or "").strip(),
        package_id=str(body.get("package_id") or "").strip(),
        epoch=epoch,
        confirmation_tag=str(body.get("confirmation_tag") or "").strip(),
        sender_account_id=ctx.account_id,
        membership_capability=_membership_capability(),
    )
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), _member_error_status(reason)
    local_peer_id = _selected_peer_id(ctx.account_id)
    return jsonify({**result, "local_peer_id": local_peer_id}), 200 if result.get("idempotent") else 201


@app.get("/rendezvous/sessions/<session_id>/security/key-confirmations")
def get_key_confirmation(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    sender_peer_id = str(request.args.get("sender_peer_id") or "").strip()
    result = svc.get_key_confirmation(
        session_id=session_id,
        requester_user_id=ctx.account_id,
        sender_peer_id=sender_peer_id,
        requester_peer_id=_requested_peer_id(),
        membership_capability=_membership_capability(),
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = _member_error_status(reason)
        return jsonify({"error": reason}), status
    return jsonify({**result, "local_peer_id": _selected_peer_id(ctx.account_id)}), 200


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
        actor_user_id=ctx.account_id,
        permissions=permissions,
        actor_peer_id=_requested_peer_id(),
        membership_capability=_membership_capability(),
    )
    if not result.get("ok"):
        reason = result["reason"]
        if reason == "permission_update_rekey_required":
            return jsonify({"error": reason, "reason_code": reason}), 409
        return jsonify({"error": reason}), _member_error_status(reason, default=404)
    local_peer_id = _selected_peer_id(ctx.account_id)
    local_session = _session_for_local_peer(result.get("session"), local_peer_id)
    return jsonify(
        {
            "ok": True,
            "local_peer_id": local_peer_id,
            "data": local_session,
        }
    ), 200


@app.delete("/rendezvous/sessions/<session_id>")
def revoke_session(session_id: str):
    ctx = _require_auth()
    if not ctx:
        return _auth_error()
    result = svc.revoke_session(
        session_id=session_id,
        actor_user_id=ctx.account_id,
        actor_peer_id=_requested_peer_id(),
        membership_capability=_membership_capability(),
    )
    if not result.get("ok"):
        reason = result["reason"]
        return jsonify({"error": reason}), _member_error_status(reason, default=404)
    log.info("session_revoked id=%s", session_id)
    return jsonify({"ok": True, "local_peer_id": result["local_peer_id"]}), 200


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
    if limited := _membership_probe_limit(ctx.account_id):
        return limited
    requested_peer_id = _requested_peer_id()
    membership_capability = _membership_capability()
    membership = svc.authenticate_session_membership(
        session_id=session_id,
        account_id=ctx.account_id,
        requested_peer_id=requested_peer_id,
        membership_capability=membership_capability,
    )
    if not membership.get("ok"):
        reason = str(membership.get("reason") or "forbidden")
        return jsonify({"error": reason}), _member_error_status(
            reason,
            default=_TURN_CREDENTIAL_ERROR_STATUS.get(reason, 409),
        )
    if limited := _rate_limit_guard(
        "turn_credentials",
        str(membership["local_peer_id"]),
        cfg.RATE_TURN_CREDENTIAL_LIMIT,
        cfg.RATE_TURN_CREDENTIAL_WINDOW,
    ):
        return limited
    result = svc.issue_turn_credentials(
        session_id=session_id,
        requester_user_id=ctx.account_id,
        requester_peer_id=requested_peer_id,
        membership_capability=membership_capability,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "turn_credentials_unavailable")
        status = _member_error_status(
            reason,
            default=_TURN_CREDENTIAL_ERROR_STATUS.get(reason, 409),
        )
        return jsonify({"error": reason}), status
    response = jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "local_peer_id": result["credentials"]["local_peer_id"],
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
    if limited := _membership_probe_limit(ctx.account_id):
        return limited
    requested_peer_id = _requested_peer_id()
    membership_capability = _membership_capability()
    membership = svc.authenticate_session_membership(
        session_id=session_id,
        account_id=ctx.account_id,
        requested_peer_id=requested_peer_id,
        membership_capability=membership_capability,
        require_pair=True,
    )
    if not membership.get("ok"):
        reason = str(membership.get("reason") or "forbidden")
        return jsonify({"error": reason}), _member_error_status(reason)
    selected_peer_id = str(membership["local_peer_id"])
    if declared_session and declared_session != session_id:
        return jsonify({"error": "signal_session_mismatch"}), 400
    if declared_sender and declared_sender != selected_peer_id:
        return jsonify({"error": "signal_sender_mismatch"}), 403
    recipient_id = str(body.get("recipient_id") or "").strip()
    if not recipient_id:
        return jsonify({"error": "recipient_id_required"}), 400
    if limited := _rate_limit_guard(
        "signal", selected_peer_id, cfg.RATE_SIGNAL_LIMIT, cfg.RATE_SIGNAL_WINDOW,
    ):
        return limited
    signal_type = str(body.get("type") or "").strip()
    result = svc.push_signal(
        session_id=session_id,
        sender_id=selected_peer_id,
        recipient_id=recipient_id,
        signal_type=signal_type,
        payload=body.get("payload"),
        sender_account_id=ctx.account_id,
        membership_capability=membership_capability,
    )
    if not result.get("ok"):
        reason = result["reason"]
        status = (
            _member_error_status(reason)
            if reason
            in {
                "forbidden",
                "local_peer_id_required",
                "membership_capability_required",
                "membership_capability_invalid",
            }
            else 400
            if reason.startswith("invalid_signal")
            else 409
        )
        return jsonify({"error": reason}), status
    return jsonify({**result, "local_peer_id": selected_peer_id}), 201


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
    if limited := _membership_probe_limit(ctx.account_id):
        return limited
    requested_peer_id = _requested_peer_id()
    membership_capability = _membership_capability()
    membership = svc.authenticate_session_membership(
        session_id=session_id,
        account_id=ctx.account_id,
        requested_peer_id=requested_peer_id,
        membership_capability=membership_capability,
        require_pair=True,
    )
    if not membership.get("ok"):
        reason = str(membership.get("reason") or "forbidden")
        return jsonify({"error": reason}), _member_error_status(reason)
    if limited := _rate_limit_guard(
        "signal_poll",
        str(membership["local_peer_id"]),
        cfg.RATE_SIGNAL_POLL_LIMIT,
        cfg.RATE_SIGNAL_POLL_WINDOW,
    ):
        return limited
    result = svc.poll_signals(
        session_id=session_id,
        user_id=ctx.account_id,
        since=since,
        requester_peer_id=requested_peer_id,
        membership_capability=membership_capability,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "forbidden")
        status = 400 if reason == "signal_cursor_invalid" else _member_error_status(reason)
        return jsonify({"error": reason}), status
    data = {key: value for key, value in result.items() if key != "ok"}
    return jsonify(
        {
            "ok": True,
            "local_peer_id": result["local_peer_id"],
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
