"""Rendezvous- und Signaling-Session-Verwaltung.

Persistenter Session-Store über SQLite (shared über mehrere Gunicorn-Worker).
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import Any

import config as cfg

log = logging.getLogger(__name__)

_lock = threading.Lock()
_db_init_lock = threading.Lock()
_db_initialized = False

# Legacy-Kompatibilität für bestehende Tests/Imports; fachlicher Zustand liegt in SQLite.
_sessions: dict[str, dict[str, Any]] = {}
_participants: dict[str, list[dict[str, Any]]] = {}
_invite_codes: dict[str, str] = {}

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0


def _now() -> float:
    return time.time()


def _sub_hash(sub: str) -> str:
    return hashlib.sha256(str(sub).encode()).hexdigest()[:16]


def _invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


@contextmanager
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        cfg.RENDEZVOUS_DB_PATH,
        timeout=cfg.RENDEZVOUS_DB_TIMEOUT_SECONDS,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def _ensure_db_initialized() -> None:
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        with _db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    owner_user_sub_hash TEXT NOT NULL,
                    owner_device_fingerprint TEXT NOT NULL,
                    oidc_issuer TEXT NOT NULL,
                    title TEXT NOT NULL,
                    invite_code TEXT UNIQUE,
                    allowed_permissions TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    revoked_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_invite ON sessions(invite_code);
                CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS participants (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_sub_hash TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_fingerprint TEXT NOT NULL,
                    permissions TEXT NOT NULL,
                    joined_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    revoked_at REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_participants_session ON participants(session_id);
                CREATE INDEX IF NOT EXISTS idx_participants_user ON participants(user_id);

                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    sent_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_signals_recipient ON signals(session_id, recipient_id, sent_at);
                """
            )
        _db_initialized = True


def _parse_permissions(raw: str | None) -> dict[str, bool]:
    if not raw:
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        return {str(k): bool(v) for k, v in data.items()}
    return {}


def _row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "owner_user_sub_hash": row["owner_user_sub_hash"],
        "owner_device_fingerprint": row["owner_device_fingerprint"],
        "oidc_issuer": row["oidc_issuer"],
        "title": row["title"],
        "invite_code": row["invite_code"] or "",
        "allowed_permissions": _parse_permissions(row["allowed_permissions"]),
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
    }


def _list_participants(conn: sqlite3.Connection, session_id: str, *, include_revoked: bool = False) -> list[dict[str, Any]]:
    where_clause = "" if include_revoked else "AND revoked_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT id, session_id, user_id, user_sub_hash, device_id, device_fingerprint, permissions, joined_at, last_seen, revoked_at
        FROM participants
        WHERE session_id = ? {where_clause}
        ORDER BY joined_at ASC
        """,
        (session_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "user_sub_hash": row["user_sub_hash"],
                "device_id": row["device_id"],
                "device_fingerprint": row["device_fingerprint"],
                "permissions": _parse_permissions(row["permissions"]),
                "joined_at": row["joined_at"],
                "last_seen": row["last_seen"],
                "revoked_at": row["revoked_at"],
            }
        )
    return out


def _session_snapshot(conn: sqlite3.Connection, session: dict[str, Any], *, include_participants: bool = False) -> dict[str, Any]:
    sid = str(session.get("id") or "")
    participants = _list_participants(conn, sid, include_revoked=False)
    permissions = dict(session.get("allowed_permissions") or {})
    out = dict(session)
    out["permissions"] = permissions
    out["participant_count"] = len(participants)
    if include_participants:
        out["participants"] = participants
    return out


def _get_session_by_id(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return _row_to_session(row)


# --- Rate limiting ---

def _rate_check(namespace: str, subject: str, limit: int, window: int) -> bool:
    if limit <= 0:
        return True
    key = f"{namespace}:{subject}"
    now = _now()
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
    now = _now()
    if now - _last_cleanup < cfg.SESSION_CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = now
    _ensure_db_initialized()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        deleted = conn.execute(
            """
            DELETE FROM sessions
            WHERE revoked_at IS NOT NULL
               OR (expires_at IS NOT NULL AND expires_at <= ?)
            """,
            (now,),
        ).rowcount
        conn.execute("COMMIT")
    if deleted:
        log.info("Cleaned up %d expired/revoked sessions", deleted)


def reset_state_for_tests() -> None:
    """Leert den persistenten Store für isolierte Tests."""
    global _last_cleanup
    _ensure_db_initialized()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM participants")
        conn.execute("DELETE FROM sessions")
        conn.execute("COMMIT")
    with _lock:
        _rate_buckets.clear()
        _sessions.clear()
        _participants.clear()
        _invite_codes.clear()
    _last_cleanup = 0.0


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
    _ensure_db_initialized()
    _cleanup_expired()
    perms = {
        "chat": True,
        "view_tui": False,
        "remote_cursor": False,
        "artifact_share": False,
        "remote_control": False,
    }
    if isinstance(allowed_permissions, dict):
        for key in perms:
            if key in allowed_permissions:
                perms[key] = bool(allowed_permissions[key])
    perms["remote_control"] = False

    sid = str(uuid.uuid4())
    now = _now()
    with _db() as conn:
        while True:
            code = _invite_code()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, owner_user_id, owner_user_sub_hash, owner_device_fingerprint, oidc_issuer,
                        title, invite_code, allowed_permissions, expires_at, created_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        sid,
                        owner_user_id,
                        _sub_hash(owner_user_sub),
                        owner_device_fingerprint,
                        oidc_issuer,
                        str(title or "Rendezvous Session")[:120],
                        code,
                        json.dumps(perms, separators=(",", ":")),
                        now + cfg.SESSION_MAX_DURATION_SECONDS,
                        now,
                    ),
                )
                conn.execute("COMMIT")
                break
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                continue
        session = _get_session_by_id(conn, sid)
        if not session:
            raise RuntimeError("created session not found")
        return _session_snapshot(conn, session, include_participants=True)


def list_sessions_for_user(*, requester_user_id: str) -> list[dict[str, Any]]:
    _ensure_db_initialized()
    _cleanup_expired()
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT s.*
            FROM sessions s
            LEFT JOIN participants p
              ON p.session_id = s.id
             AND p.revoked_at IS NULL
            WHERE s.revoked_at IS NULL
              AND (s.owner_user_id = ? OR p.user_id = ?)
            ORDER BY s.created_at DESC
            """,
            (requester_user_id, requester_user_id),
        ).fetchall()
        return [_session_snapshot(conn, _row_to_session(row), include_participants=True) for row in rows]


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
    _ensure_db_initialized()
    _cleanup_expired()
    code = str(invite_code or "").strip().upper()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE invite_code = ? AND invite_code IS NOT NULL AND invite_code != ''",
            (code,),
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "invalid_invite_code"}
        session = _row_to_session(row)
        sid = str(session.get("id") or "")
        if expected_session_id and sid != expected_session_id:
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

        existing = conn.execute(
            """
            SELECT id, session_id, user_id, user_sub_hash, device_id, device_fingerprint, permissions, joined_at, last_seen, revoked_at
            FROM participants
            WHERE session_id = ? AND user_id = ? AND device_id = ? AND revoked_at IS NULL
            LIMIT 1
            """,
            (sid, user_id, device_id),
        ).fetchone()
        if existing:
            participant = {
                "id": existing["id"],
                "session_id": existing["session_id"],
                "user_id": existing["user_id"],
                "user_sub_hash": existing["user_sub_hash"],
                "device_id": existing["device_id"],
                "device_fingerprint": existing["device_fingerprint"],
                "permissions": _parse_permissions(existing["permissions"]),
                "joined_at": existing["joined_at"],
                "last_seen": existing["last_seen"],
                "revoked_at": existing["revoked_at"],
            }
            return {"ok": True, "participant": participant, "idempotent": True}

        active_count = conn.execute(
            "SELECT COUNT(1) AS c FROM participants WHERE session_id = ? AND revoked_at IS NULL",
            (sid,),
        ).fetchone()
        if int(active_count["c"] or 0) >= 20:
            return {"ok": False, "reason": "session_full"}

        participant = {
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
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO participants (
                id, session_id, user_id, user_sub_hash, device_id, device_fingerprint,
                permissions, joined_at, last_seen, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                participant["id"],
                participant["session_id"],
                participant["user_id"],
                participant["user_sub_hash"],
                participant["device_id"],
                participant["device_fingerprint"],
                json.dumps(participant["permissions"], separators=(",", ":")),
                participant["joined_at"],
                participant["last_seen"],
            ),
        )
        conn.execute("COMMIT")
        return {"ok": True, "participant": dict(participant)}


def update_session_permissions(
    *,
    session_id: str,
    actor_user_id: str,
    permissions: dict[str, bool],
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("owner_user_id") != actor_user_id:
            return {"ok": False, "reason": "forbidden"}
        current = dict(session.get("allowed_permissions") or {})
        for key in current:
            if key in permissions:
                current[key] = bool(permissions[key])
        current["remote_control"] = False
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE sessions SET allowed_permissions = ? WHERE id = ?",
            (json.dumps(current, separators=(",", ":")), session_id),
        )
        conn.execute(
            "UPDATE participants SET permissions = ? WHERE session_id = ? AND revoked_at IS NULL",
            (json.dumps(current, separators=(",", ":")), session_id),
        )
        conn.execute("COMMIT")
        updated = _get_session_by_id(conn, session_id)
        if not updated:
            return {"ok": False, "reason": "session_not_found"}
        return {"ok": True, "session": _session_snapshot(conn, updated, include_participants=True)}


def get_participants(*, session_id: str, requester_user_id: str) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        requester_is_member = bool(
            session.get("owner_user_id") == requester_user_id
            or conn.execute(
                """
                SELECT 1
                FROM participants
                WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL
                LIMIT 1
                """,
                (session_id, requester_user_id),
            ).fetchone()
        )
        if not requester_is_member:
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
            for p in _list_participants(conn, session_id, include_revoked=True)
        ]
        return {"ok": True, "participants": presence}


def touch_participant(*, session_id: str, user_id: str) -> None:
    _ensure_db_initialized()
    with _db() as conn:
        conn.execute(
            """
            UPDATE participants
            SET last_seen = ?
            WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (_now(), session_id, user_id),
        )


def revoke_session(*, session_id: str, actor_user_id: str) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("owner_user_id") != actor_user_id:
            return {"ok": False, "reason": "forbidden"}
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE sessions SET revoked_at = ?, invite_code = '' WHERE id = ?",
            (_now(), session_id),
        )
        conn.execute("COMMIT")
    return {"ok": True}


def is_authorized_participant(session_id: str, user_id: str) -> bool:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session or session.get("revoked_at") is not None:
            return False
        if str(session.get("owner_user_id") or "") == user_id:
            return True
        row = conn.execute(
            """
            SELECT 1 FROM participants
            WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL
            LIMIT 1
            """,
            (session_id, user_id),
        ).fetchone()
        return bool(row)


# --- WebRTC signaling ---

_MAX_SIGNAL_QUEUE = 20
_MAX_SIGNAL_BYTES = 8 * 1024


def push_signal(*, session_id: str, sender_id: str, recipient_id: str, signal_type: str, payload: Any) -> dict[str, Any]:
    _ensure_db_initialized()
    if not is_authorized_participant(session_id, sender_id):
        return {"ok": False, "reason": "forbidden"}
    if not is_authorized_participant(session_id, recipient_id):
        return {"ok": False, "reason": "recipient_not_authorized"}
    if signal_type not in {"offer", "answer", "ice_candidate"}:
        return {"ok": False, "reason": "invalid_signal_type"}
    entry = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "type": signal_type,
        "payload": payload,
        "sent_at": _now(),
    }
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO signals (id, session_id, sender_id, recipient_id, signal_type, payload, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["id"],
                entry["session_id"],
                entry["sender_id"],
                entry["recipient_id"],
                entry["type"],
                json.dumps(entry["payload"]),
                entry["sent_at"],
            ),
        )
        conn.execute(
            """
            DELETE FROM signals
            WHERE id IN (
                SELECT id
                FROM signals
                WHERE session_id = ? AND recipient_id = ?
                ORDER BY sent_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (session_id, recipient_id, _MAX_SIGNAL_QUEUE),
        )
        conn.execute("COMMIT")
    return {"ok": True, "signal_id": entry["id"]}


def consume_signals(*, session_id: str, user_id: str) -> list[dict[str, Any]]:
    _ensure_db_initialized()
    if not is_authorized_participant(session_id, user_id):
        return []
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, sender_id, recipient_id, signal_type, payload, sent_at
            FROM signals
            WHERE session_id = ? AND recipient_id = ?
            ORDER BY sent_at ASC
            """,
            (session_id, user_id),
        ).fetchall()
        signal_ids = [str(row["id"]) for row in rows]
        if signal_ids:
            placeholders = ",".join("?" for _ in signal_ids)
            conn.execute(f"DELETE FROM signals WHERE id IN ({placeholders})", tuple(signal_ids))
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "sender_id": row["sender_id"],
                    "recipient_id": row["recipient_id"],
                    "type": row["signal_type"],
                    "payload": json.loads(row["payload"]),
                    "sent_at": row["sent_at"],
                }
            )
        return out


# --- TURN credentials (coturn REST API format) ---

def issue_turn_credentials(user_id: str) -> dict[str, Any] | None:
    """Gibt kurzlebige TURN-Credentials aus (coturn HMAC-SHA1 REST API Format)."""
    import base64
    import hmac

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


_ensure_db_initialized()
