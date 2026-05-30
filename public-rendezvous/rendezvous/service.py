"""Rendezvous- und Signaling-Session-Verwaltung (in-memory, standalone).

Kein Ananta-Agent-Import – läuft eigenständig.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from collections import defaultdict
from typing import Any

import config as cfg

log = logging.getLogger(__name__)

_lock = threading.Lock()

_sessions: dict[str, dict[str, Any]] = {}
_participants: dict[str, list[dict[str, Any]]] = {}
_invite_codes: dict[str, str] = {}       # code -> session_id
_signal_queues: dict[str, list[dict[str, Any]]] = defaultdict(list)  # "sess:user" -> signals

_rate_buckets: dict[str, list[float]] = defaultdict(list)

_last_cleanup: float = 0.0


# --- Rate limiting ---

def _rate_check(namespace: str, subject: str, limit: int, window: int) -> bool:
    if limit <= 0:
        return True
    key = f"{namespace}:{subject}"
    now = time.time()
    with _lock:
        bucket = _rate_buckets[key]
        bucket[:] = [t for t in bucket if now - t < window]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
    return True


# --- Cleanup ---

def _cleanup_expired() -> None:
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < cfg.SESSION_CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = now
    with _lock:
        expired_ids = [
            sid for sid, s in _sessions.items()
            if (s.get("revoked_at") is not None) or
               (isinstance(s.get("expires_at"), (int, float)) and float(s["expires_at"]) <= now)
        ]
        for sid in expired_ids:
            s = _sessions.pop(sid, None)
            _participants.pop(sid, None)
            code = (s or {}).get("invite_code", "")
            if code and _invite_codes.get(code) == sid:
                del _invite_codes[code]
    if expired_ids:
        log.info("Cleaned up %d expired sessions", len(expired_ids))


# --- Helpers ---

def _invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _now() -> float:
    return time.time()


def _sub_hash(sub: str) -> str:
    return hashlib.sha256(str(sub).encode()).hexdigest()[:16]


# --- Session operations ---

def create_session(
    *,
    owner_user_id: str,
    owner_user_sub: str,
    owner_device_fingerprint: str,
    oidc_issuer: str,
    allowed_permissions: dict[str, bool] | None = None,
    title: str = "Rendezvous Session",
) -> dict[str, Any]:
    _cleanup_expired()
    perms = {
        "chat": True,
        "view_tui": False,
        "remote_cursor": False,
        "artifact_share": False,
        "remote_control": False,
    }
    if isinstance(allowed_permissions, dict):
        for k in perms:
            if k in allowed_permissions:
                perms[k] = bool(allowed_permissions[k])
    perms["remote_control"] = False

    sid = str(uuid.uuid4())
    with _lock:
        code = _invite_code()
        while code in _invite_codes:
            code = _invite_code()
        session: dict[str, Any] = {
            "id": sid,
            "owner_user_id": owner_user_id,
            "owner_user_sub_hash": _sub_hash(owner_user_sub),
            "owner_device_fingerprint": owner_device_fingerprint,
            "oidc_issuer": oidc_issuer,
            "title": str(title or "Rendezvous Session")[:120],
            "invite_code": code,
            "allowed_permissions": perms,
            "expires_at": _now() + cfg.SESSION_MAX_DURATION_SECONDS,
            "created_at": _now(),
            "revoked_at": None,
        }
        _sessions[sid] = session
        _invite_codes[code] = sid
        _participants[sid] = []
    return _session_snapshot(session, include_participants=True)


def _session_snapshot(session: dict[str, Any], *, include_participants: bool = False) -> dict[str, Any]:
    """Return the public API shape for a session."""
    sid = str(session.get("id") or "")
    participants = [dict(p) for p in _participants.get(sid, []) if not p.get("revoked_at")]
    permissions = dict(session.get("allowed_permissions") or {})
    out = dict(session)
    out["permissions"] = permissions
    out["participant_count"] = len(participants)
    if include_participants:
        out["participants"] = participants
    return out


def list_sessions_for_user(*, requester_user_id: str) -> list[dict[str, Any]]:
    _cleanup_expired()
    with _lock:
        out: list[dict[str, Any]] = []
        for sid, session in _sessions.items():
            if session.get("revoked_at") is not None:
                continue
            parts = _participants.get(sid, [])
            is_member = (
                session.get("owner_user_id") == requester_user_id
                or any(p.get("user_id") == requester_user_id and not p.get("revoked_at") for p in parts)
            )
            if is_member:
                out.append(_session_snapshot(session, include_participants=True))
        out.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return out


def join_session(
    *,
    invite_code: str,
    user_id: str,
    user_sub: str,
    device_id: str,
    device_fingerprint: str,
    oidc_issuer: str,
    expected_session_id: str = "",
) -> dict[str, Any]:
    _cleanup_expired()
    code = str(invite_code or "").strip().upper()
    with _lock:
        sid = _invite_codes.get(code)
        if not sid:
            return {"ok": False, "reason": "invalid_invite_code"}
        if expected_session_id and sid != expected_session_id:
            return {"ok": False, "reason": "session_not_found"}
        session = _sessions.get(sid)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None:
            return {"ok": False, "reason": "session_revoked"}
        if float(session.get("expires_at") or 0) < _now():
            return {"ok": False, "reason": "session_expired"}
        sess_issuer = str(session.get("oidc_issuer") or "")
        if sess_issuer and sess_issuer != str(oidc_issuer or ""):
            return {"ok": False, "reason": "oidc_issuer_mismatch"}
        if not str(user_sub or "").strip():
            return {"ok": False, "reason": "oidc_sub_required"}
        existing = _participants.get(sid, [])
        for p in existing:
            if p.get("user_id") == user_id and p.get("device_id") == device_id and not p.get("revoked_at"):
                return {"ok": True, "participant": dict(p), "idempotent": True}
        if len(existing) >= 20:
            return {"ok": False, "reason": "session_full"}
        participant: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "session_id": sid,
            "user_id": user_id,
            "user_sub_hash": _sub_hash(user_sub),
            "device_id": device_id,
            "device_fingerprint": device_fingerprint,
            "permissions": dict(session.get("allowed_permissions") or {}),
            "joined_at": _now(),
            "last_seen": _now(),
            "revoked_at": None,
        }
        _participants[sid].append(participant)
        return {"ok": True, "participant": dict(participant)}


def update_session_permissions(
    *,
    session_id: str,
    actor_user_id: str,
    permissions: dict[str, bool],
) -> dict[str, Any]:
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("owner_user_id") != actor_user_id:
            return {"ok": False, "reason": "forbidden"}
        current = dict(session.get("allowed_permissions") or {})
        for key in current:
            if key in permissions:
                current[key] = bool(permissions[key])
        current["remote_control"] = False
        session["allowed_permissions"] = current
        return {"ok": True, "session": _session_snapshot(session, include_participants=True)}


def get_participants(*, session_id: str, requester_user_id: str) -> dict[str, Any]:
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        parts = _participants.get(session_id, [])
        is_member = (
            session.get("owner_user_id") == requester_user_id
            or any(p.get("user_id") == requester_user_id and not p.get("revoked_at") for p in parts)
        )
        if not is_member:
            return {"ok": False, "reason": "forbidden"}
        presence = [
            {
                "user_id": p.get("user_id"),
                "device_fingerprint": p.get("device_fingerprint"),
                "permissions": p.get("permissions"),
                "joined_at": p.get("joined_at"),
                "last_seen": p.get("last_seen"),
                "revoked_at": p.get("revoked_at"),
            }
            for p in parts
        ]
        return {"ok": True, "participants": presence}


def touch_participant(*, session_id: str, user_id: str) -> None:
    with _lock:
        for p in _participants.get(session_id, []):
            if p.get("user_id") == user_id and not p.get("revoked_at"):
                p["last_seen"] = _now()


def revoke_session(*, session_id: str, actor_user_id: str) -> dict[str, Any]:
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("owner_user_id") != actor_user_id:
            return {"ok": False, "reason": "forbidden"}
        session["revoked_at"] = _now()
        code = str(session.get("invite_code") or "")
        if code and _invite_codes.get(code) == session_id:
            del _invite_codes[code]
    return {"ok": True}


def is_authorized_participant(session_id: str, user_id: str) -> bool:
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return False
        if str(session.get("owner_user_id") or "") == user_id:
            return True
        return any(
            str(p.get("user_id") or "") == user_id and not p.get("revoked_at")
            for p in _participants.get(session_id, [])
        )


# --- WebRTC signaling ---

_MAX_SIGNAL_QUEUE = 20
_MAX_SIGNAL_BYTES = 8 * 1024


def push_signal(*, session_id: str, sender_id: str, recipient_id: str, signal_type: str, payload: Any) -> dict[str, Any]:
    if not is_authorized_participant(session_id, sender_id):
        return {"ok": False, "reason": "forbidden"}
    if not is_authorized_participant(session_id, recipient_id):
        return {"ok": False, "reason": "recipient_not_authorized"}
    if signal_type not in {"offer", "answer", "ice_candidate"}:
        return {"ok": False, "reason": "invalid_signal_type"}
    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "type": signal_type,
        "payload": payload,
        "sent_at": _now(),
    }
    key = f"{session_id}:{recipient_id}"
    with _lock:
        _signal_queues[key].append(entry)
        _signal_queues[key] = _signal_queues[key][-_MAX_SIGNAL_QUEUE:]
    return {"ok": True, "signal_id": entry["id"]}


def consume_signals(*, session_id: str, user_id: str) -> list[dict[str, Any]]:
    if not is_authorized_participant(session_id, user_id):
        return []
    key = f"{session_id}:{user_id}"
    with _lock:
        signals = list(_signal_queues.get(key) or [])
        _signal_queues[key] = []
    return signals


# --- TURN credentials (coturn REST API format) ---

def issue_turn_credentials(user_id: str) -> dict[str, Any] | None:
    """Gibt kurzlebige TURN-Credentials aus (coturn HMAC-SHA1 REST API Format)."""
    import base64
    import hmac
    import hashlib
    secret = cfg.TURN_SHARED_SECRET
    if not secret:
        return None
    ttl = cfg.TURN_TTL_SECONDS
    expiry = int(_now()) + ttl
    username = f"{expiry}:{user_id}"
    key = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    password = base64.b64encode(key).decode()
    return {
        "username": username,
        "password": password,
        "ttl": ttl,
        "uris": cfg.TURN_URLS,
    }
