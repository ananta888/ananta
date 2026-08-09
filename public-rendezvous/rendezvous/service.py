"""Rendezvous- und Signaling-Session-Verwaltung.

Persistenter Session-Store über SQLite (shared über mehrere Gunicorn-Worker).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import Any

from pair_security import (
    PUBLIC_MEDIA_E2EE_VERSION,
    TENANT_ID,
    PairSecurityAuthority,
    normalize_public_media_advertisement,
    public_media_capabilities_v1,
    spki_fingerprint,
)
from peer_identity import (
    canonical_device_peer_id,
    is_canonical_account_id,
    is_device_peer_id,
    is_membership_capability,
    membership_capability_digest,
    owner_capability_lookup_digest,
)

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

_KEY_CONFIRMATION_TTL_SECONDS = 5 * 60
_CAPABILITY_TOMBSTONE_TTL_SECONDS = max(cfg.SESSION_MAX_DURATION_SECONDS, 24 * 60 * 60)
_MAX_SIGNAL_QUEUE = 256
_MAX_SIGNAL_BYTES = 8 * 1024
_MAX_SIGNAL_CURSOR = (1 << 63) - 1
_PROTOCOL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")
_SECURITY_AUTHORITY = PairSecurityAuthority(cfg.RENDEZVOUS_SECURITY_SIGNING_SECRET)
_SECURITY_AUTHORITY.require_key_id(cfg.RENDEZVOUS_EXPECTED_SIGNING_KEY_ID)


def _now() -> float:
    return time.time()


def _subject_hash(issuer: str, sub: str) -> str:
    """Persist only a pseudonymous digest of the canonical OIDC identity."""
    material = (
        b"ananta.public-rendezvous.subject-hash.v1\0"
        + str(issuer or "").strip().rstrip("/").encode("utf-8")
        + b"\0"
        + str(sub or "").strip().encode("utf-8")
    )
    return hashlib.sha256(material).hexdigest()[:16]


def _owner_create_request_digest(
    *,
    account_id: str,
    peer_id: str,
    subject_hash: str,
    device_id: str,
    device_fingerprint: str,
    public_key_spki_b64: str,
    oidc_issuer: str,
    title: str,
    permissions: dict[str, bool],
    requested_expires_at: float | None,
    public_media_e2ee_version: int,
) -> str:
    payload = {
        "account_id": account_id,
        "device_fingerprint": device_fingerprint,
        "device_id": device_id,
        "identity_binding_version": 2,
        "oidc_issuer": oidc_issuer.rstrip("/"),
        "peer_id": peer_id,
        "permissions": permissions,
        "public_key_spki_b64": public_key_spki_b64,
        "requested_expires_at": requested_expires_at,
        "subject_hash": subject_hash,
        "title": title,
    }
    # Preserve the byte-for-byte v2 digest for pre-media clients and already
    # persisted idempotency records. The media fields are bound only when the
    # owner explicitly opts into the closed v1 capability set.
    if public_media_e2ee_version == PUBLIC_MEDIA_E2EE_VERSION:
        payload["public_media_e2ee_version"] = public_media_e2ee_version
        payload["public_media_capabilities"] = public_media_capabilities_v1()
    material = b"ananta.public-rendezvous.owner-create-request.v2\0" + json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _is_canonical_peer_id(value: str) -> bool:
    """Backward-compatible v1 name for the account-scoped identifier."""
    return is_canonical_account_id(value)


def _is_protocol_identifier(value: str) -> bool:
    """Match the closed identifier grammar consumed by signed Pair packages."""
    return _PROTOCOL_IDENTIFIER.fullmatch(str(value or "")) is not None


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
            _migrate_database(conn)
        _db_initialized = True


def _migrate_database(conn: sqlite3.Connection) -> None:
    """Apply schema and cursor migration under one database-wide writer lock."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        _create_base_schema(conn)
        _add_column(conn, "sessions", "owner_device_id TEXT NOT NULL DEFAULT 'owner-device'")
        _add_column(conn, "sessions", "owner_public_key_spki_b64 TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sessions", "security_epoch INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "sessions", "security_mode TEXT NOT NULL DEFAULT 'legacy'")
        _add_column(conn, "sessions", "security_contract_version INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "sessions", "identity_binding_version INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "sessions", "owner_account_id TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sessions", "owner_peer_id TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sessions", "owner_membership_capability_hash TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sessions", "owner_capability_lookup_hash TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sessions", "owner_create_request_hash TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sessions", "public_media_e2ee_version INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "participants", "public_key_spki_b64 TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "participants", "account_id TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "participants", "peer_id TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "participants", "membership_capability_hash TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "participants", "public_media_e2ee_version INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "signals", "sequence INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "key_confirmations", "expires_at REAL NOT NULL DEFAULT 0")
        _backfill_v1_identity_columns(conn)
        _backfill_signal_sequences(conn)
        # Compatibility boundary: the pre-cursor image writes the column
        # default (0). A persistent unique index would make rollback fail on
        # its second signal. New writers serialize allocation with BEGIN
        # IMMEDIATE, so the index is unnecessary.
        conn.execute("DROP INDEX IF EXISTS idx_signals_recipient_sequence")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner_account ON sessions(owner_account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_participants_account ON participants(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_participants_peer ON participants(session_id, peer_id)")
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_owner_capability_lookup
               ON sessions(owner_capability_lookup_hash)
               WHERE owner_capability_lookup_hash != ''"""
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _create_base_schema(conn: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS sessions (
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
           )""",
        "CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_invite ON sessions(invite_code)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)",
        """CREATE TABLE IF NOT EXISTS participants (
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
           )""",
        "CREATE INDEX IF NOT EXISTS idx_participants_session ON participants(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_participants_user ON participants(user_id)",
        """CREATE TABLE IF NOT EXISTS signals (
               id TEXT PRIMARY KEY,
               session_id TEXT NOT NULL,
               sender_id TEXT NOT NULL,
               recipient_id TEXT NOT NULL,
               signal_type TEXT NOT NULL,
               payload TEXT NOT NULL,
               sequence INTEGER NOT NULL DEFAULT 0,
               sent_at REAL NOT NULL,
               FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
           )""",
        "CREATE INDEX IF NOT EXISTS idx_signals_recipient ON signals(session_id, recipient_id, sent_at)",
        """CREATE TABLE IF NOT EXISTS key_confirmations (
               session_id TEXT NOT NULL,
               epoch INTEGER NOT NULL,
               sender_peer_id TEXT NOT NULL,
               recipient_peer_id TEXT NOT NULL,
               package_id TEXT NOT NULL,
               confirmation_tag TEXT NOT NULL,
               created_at REAL NOT NULL,
               expires_at REAL NOT NULL DEFAULT 0,
               PRIMARY KEY(session_id, epoch, sender_peer_id, recipient_peer_id),
               FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
           )""",
        """CREATE TABLE IF NOT EXISTS retired_owner_capabilities (
               owner_capability_lookup_hash TEXT PRIMARY KEY,
               retired_at REAL NOT NULL
           )""",
    )
    for statement in statements:
        conn.execute(statement)


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        except sqlite3.OperationalError as exc:
            # Keep retries idempotent for externally managed legacy schemas,
            # but propagate every error that did not actually add the column.
            refreshed = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if name not in refreshed:
                raise exc


def _backfill_signal_sequences(conn: sqlite3.Connection) -> None:
    """Assign deterministic per-recipient cursors to rows from the legacy schema."""
    rows = conn.execute(
        """SELECT rowid, session_id, recipient_id, sequence
           FROM signals
           ORDER BY session_id, recipient_id, sent_at, rowid"""
    ).fetchall()
    partition: tuple[str, str] | None = None
    high_watermark = 0
    for row in rows:
        current_partition = (str(row["session_id"]), str(row["recipient_id"]))
        if current_partition != partition:
            partition = current_partition
            high_watermark = 0
        stored_sequence = int(row["sequence"] or 0)
        if stored_sequence <= high_watermark:
            stored_sequence = high_watermark + 1
            conn.execute(
                "UPDATE signals SET sequence = ? WHERE rowid = ?",
                (stored_sequence, int(row["rowid"])),
            )
        high_watermark = stored_sequence


def _backfill_v1_identity_columns(conn: sqlite3.Connection) -> None:
    """Populate additive principal/peer columns for already strict v1 rows."""
    conn.execute(
        """UPDATE sessions
           SET identity_binding_version = 1
           WHERE identity_binding_version = 0
             AND security_mode = 'strict_e2ee'
             AND security_contract_version = 1"""
    )
    conn.execute(
        """UPDATE sessions
           SET owner_account_id = owner_user_id,
               owner_peer_id = owner_user_id
           WHERE identity_binding_version = 1
             AND (owner_account_id = '' OR owner_peer_id = '')"""
    )
    conn.execute(
        """UPDATE participants
           SET account_id = user_id,
               peer_id = user_id
           WHERE session_id IN (
               SELECT id FROM sessions WHERE identity_binding_version = 1
           )
             AND (account_id = '' OR peer_id = '')"""
    )


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
        "owner_device_id": row["owner_device_id"],
        "owner_public_key_spki_b64": row["owner_public_key_spki_b64"],
        "owner_account_id": row["owner_account_id"],
        "owner_peer_id": row["owner_peer_id"],
        "_owner_membership_capability_hash": row["owner_membership_capability_hash"],
        "_owner_capability_lookup_hash": row["owner_capability_lookup_hash"],
        "_owner_create_request_hash": row["owner_create_request_hash"],
        "security_epoch": row["security_epoch"],
        "security_contract_version": row["security_contract_version"],
        "identity_binding_version": row["identity_binding_version"],
        "public_media_e2ee_version": row["public_media_e2ee_version"],
        "public_media_capabilities": (
            public_media_capabilities_v1()
            if int(row["public_media_e2ee_version"] or 0) == PUBLIC_MEDIA_E2EE_VERSION
            else None
        ),
        "security_mode": row["security_mode"],
        "mode": "p2p",
        "transport": "webrtc",
        "permissions_version": 1,
    }


def _list_participants(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    where_clause = "" if include_revoked else "AND revoked_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT id, session_id, user_id, user_sub_hash, device_id,
               device_fingerprint, public_key_spki_b64, permissions,
               joined_at, last_seen, revoked_at, account_id, peer_id,
               membership_capability_hash, public_media_e2ee_version
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
                "public_key_spki_b64": row["public_key_spki_b64"],
                "account_id": row["account_id"],
                "peer_id": row["peer_id"],
                "_membership_capability_hash": row["membership_capability_hash"],
                "public_media_e2ee_version": row["public_media_e2ee_version"],
                "public_media_capabilities": (
                    public_media_capabilities_v1()
                    if int(row["public_media_e2ee_version"] or 0) == PUBLIC_MEDIA_E2EE_VERSION
                    else None
                ),
            }
        )
    return out


def _session_snapshot(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    include_participants: bool = False,
) -> dict[str, Any]:
    sid = str(session.get("id") or "")
    participants = _list_participants(conn, sid, include_revoked=False)
    permissions = dict(session.get("allowed_permissions") or {})
    out = {key: value for key, value in session.items() if not key.startswith("_")}
    out["permissions"] = permissions
    out["participant_count"] = len(participants)
    if include_participants:
        out["participants"] = [
            {key: value for key, value in participant.items() if not key.startswith("_")}
            for participant in participants
        ]
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
        conn.execute(
            "DELETE FROM retired_owner_capabilities WHERE retired_at <= ?",
            (now - _CAPABILITY_TOMBSTONE_TTL_SECONDS,),
        )
        conn.execute(
            """INSERT OR IGNORE INTO retired_owner_capabilities (
                   owner_capability_lookup_hash, retired_at
               )
               SELECT owner_capability_lookup_hash, ?
               FROM sessions
               WHERE owner_capability_lookup_hash != ''
                 AND (revoked_at IS NOT NULL OR expires_at <= ?)""",
            (now, now),
        )
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
        conn.execute("DELETE FROM key_confirmations")
        conn.execute("DELETE FROM participants")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM retired_owner_capabilities")
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
    owner_device_id: str = "",
    owner_public_key_spki_b64: str = "",
    oidc_issuer: str,
    allowed_permissions: dict[str, bool] | None = None,
    title: str = "Rendezvous Session",
    requested_expires_at: float | None = None,
    identity_binding_version: int = 1,
    membership_capability: str = "",
    public_media_e2ee_version: int | None = None,
    public_media_capabilities: Any = None,
) -> dict[str, Any]:
    _ensure_db_initialized()
    _cleanup_expired()
    owner_device_id = str(owner_device_id or "").strip()
    owner_device_fingerprint = str(owner_device_fingerprint or "").strip().lower()
    owner_public_key_spki_b64 = str(owner_public_key_spki_b64 or "").strip()
    oidc_issuer = str(oidc_issuer or "").strip()
    owner_user_sub = str(owner_user_sub or "").strip()
    if not is_canonical_account_id(owner_user_id) or not owner_user_sub or not oidc_issuer:
        raise ValueError("oidc_identity_required")
    if identity_binding_version not in {1, 2}:
        raise ValueError("identity_binding_version_unsupported")
    normalized_media_version = normalize_public_media_advertisement(
        public_media_e2ee_version,
        public_media_capabilities,
    )
    if normalized_media_version and identity_binding_version != 2:
        raise ValueError("public_media_identity_binding_v2_required")
    membership_capability = str(membership_capability or "").strip()
    if identity_binding_version == 2:
        if not membership_capability:
            raise ValueError("membership_capability_required")
        if not is_membership_capability(membership_capability):
            raise ValueError("membership_capability_invalid")
    if not owner_device_id or not owner_device_fingerprint or not owner_public_key_spki_b64:
        raise ValueError("device_identity_required")
    if not _is_protocol_identifier(owner_device_id):
        raise ValueError("device_identity_invalid")
    if not secrets.compare_digest(spki_fingerprint(owner_public_key_spki_b64), owner_device_fingerprint):
        raise ValueError("device_key_substitution")
    owner_peer_id = (
        canonical_device_peer_id(owner_user_id, owner_device_fingerprint)
        if identity_binding_version == 2
        else owner_user_id
    )
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

    now = _now()
    if requested_expires_at is None:
        expires_at = now + cfg.SESSION_MAX_DURATION_SECONDS
    elif (
        isinstance(requested_expires_at, bool)
        or not isinstance(requested_expires_at, (int, float))
        or not math.isfinite(float(requested_expires_at))
        or float(requested_expires_at) <= now
    ):
        raise ValueError("session_expiry_invalid")
    else:
        expires_at = min(
            float(requested_expires_at),
            now + cfg.SESSION_MAX_DURATION_SECONDS,
        )

    normalized_title = str(title or "Rendezvous Session")[:120]
    owner_subject_hash = _subject_hash(oidc_issuer, owner_user_sub)
    create_request_hash = (
        _owner_create_request_digest(
            account_id=owner_user_id,
            peer_id=owner_peer_id,
            subject_hash=owner_subject_hash,
            device_id=owner_device_id,
            device_fingerprint=owner_device_fingerprint,
            public_key_spki_b64=owner_public_key_spki_b64,
            oidc_issuer=oidc_issuer,
            title=normalized_title,
            permissions=perms,
            requested_expires_at=(float(requested_expires_at) if requested_expires_at is not None else None),
            public_media_e2ee_version=normalized_media_version,
        )
        if identity_binding_version == 2
        else ""
    )
    sid = str(uuid.uuid4())
    membership_capability = membership_capability if identity_binding_version == 2 else ""
    capability_lookup_hash = (
        owner_capability_lookup_digest(owner_user_id, owner_peer_id, membership_capability)
        if membership_capability
        else ""
    )
    capability_hash = (
        membership_capability_digest(sid, owner_user_id, owner_peer_id, membership_capability)
        if membership_capability
        else ""
    )
    with _db() as conn:
        while True:
            code = _invite_code()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if capability_lookup_hash:
                    retired = conn.execute(
                        """SELECT 1 FROM retired_owner_capabilities
                           WHERE owner_capability_lookup_hash = ? LIMIT 1""",
                        (capability_lookup_hash,),
                    ).fetchone()
                    if retired:
                        conn.execute("ROLLBACK")
                        raise ValueError("membership_capability_retired")
                    existing_row = conn.execute(
                        "SELECT * FROM sessions WHERE owner_capability_lookup_hash = ? LIMIT 1",
                        (capability_lookup_hash,),
                    ).fetchone()
                    if existing_row:
                        existing = _row_to_session(existing_row)
                        if existing.get("revoked_at") is not None or float(existing.get("expires_at") or 0) <= _now():
                            conn.execute("ROLLBACK")
                            raise ValueError("membership_capability_retired")
                        expected_hash = membership_capability_digest(
                            str(existing["id"]),
                            owner_user_id,
                            owner_peer_id,
                            membership_capability,
                        )
                        immutable_matches = (
                            secrets.compare_digest(
                                str(existing.get("_owner_create_request_hash") or ""),
                                create_request_hash,
                            )
                            and int(existing.get("identity_binding_version") or 0) == 2
                            and int(existing.get("public_media_e2ee_version") or 0) == normalized_media_version
                            and secrets.compare_digest(
                                str(existing.get("_owner_membership_capability_hash") or ""),
                                expected_hash,
                            )
                        )
                        if not immutable_matches:
                            conn.execute("ROLLBACK")
                            raise ValueError("membership_capability_conflict")
                        conn.execute("COMMIT")
                        snapshot = _session_snapshot(conn, existing, include_participants=True)
                        snapshot["_idempotent"] = True
                        return snapshot
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, owner_user_id, owner_user_sub_hash, owner_device_fingerprint, oidc_issuer,
                        title, invite_code, allowed_permissions, expires_at, created_at, revoked_at,
                        owner_device_id, owner_public_key_spki_b64, security_epoch,
                        security_mode, security_contract_version, identity_binding_version,
                        owner_account_id, owner_peer_id, owner_membership_capability_hash,
                        owner_capability_lookup_hash, owner_create_request_hash,
                        public_media_e2ee_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        owner_user_id,
                        owner_subject_hash,
                        owner_device_fingerprint,
                        oidc_issuer,
                        normalized_title,
                        code,
                        json.dumps(perms, separators=(",", ":")),
                        expires_at,
                        now,
                        owner_device_id,
                        owner_public_key_spki_b64,
                        "strict_e2ee",
                        1,
                        identity_binding_version,
                        owner_user_id,
                        owner_peer_id,
                        capability_hash,
                        capability_lookup_hash,
                        create_request_hash,
                        normalized_media_version,
                    ),
                )
                conn.execute("COMMIT")
                break
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                if "sessions.invite_code" in str(exc):
                    continue
                if capability_lookup_hash:
                    raced = conn.execute(
                        "SELECT 1 FROM sessions WHERE owner_capability_lookup_hash = ? LIMIT 1",
                        (capability_lookup_hash,),
                    ).fetchone()
                    if raced:
                        # Re-enter the locked lookup path, which verifies the
                        # full capability and immutable request digest.
                        continue
                raise
        session = _get_session_by_id(conn, sid)
        if not session:
            raise RuntimeError("created session not found")
        snapshot = _session_snapshot(conn, session, include_participants=True)
        return snapshot


def list_sessions_for_user(
    *,
    requester_user_id: str,
    requested_peer_id: str = "",
    requested_device_id: str = "",
) -> list[dict[str, Any]]:
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
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            session = _row_to_session(row)
            snapshot = _session_snapshot(conn, session, include_participants=True)
            try:
                memberships = _validated_strict_memberships(conn, session, require_pair=False)
            except ValueError:
                # Legacy/incomplete sessions remain visible for cleanup, but
                # never receive an inferred authenticated transport identity.
                memberships = []
            account_members = [member for member in memberships if member["account_id"] == requester_user_id]
            local_peer_ids = [str(member["peer_id"]) for member in account_members]
            selected = next(
                (member for member in account_members if requested_peer_id and member["peer_id"] == requested_peer_id),
                None,
            )
            if selected is None and requested_device_id:
                device_matches = [member for member in account_members if member["device_id"] == requested_device_id]
                selected = device_matches[0] if len(device_matches) == 1 else None
            if (
                selected is None
                and int(session.get("identity_binding_version") or 0) == 1
                and len(account_members) == 1
            ):
                selected = account_members[0]
            snapshot["local_peer_ids"] = local_peer_ids
            snapshot["local_peer_id"] = str(selected["peer_id"]) if selected else None
            snapshots.append(snapshot)
        return snapshots


def is_owner_create_recovery(
    *,
    account_id: str,
    device_fingerprint: str,
    membership_capability: str,
    public_media_e2ee_version: int = 0,
) -> bool:
    """Recognize a v2 create retry so rate limiting does not orphan it."""
    try:
        peer_id = canonical_device_peer_id(account_id, device_fingerprint)
        lookup_hash = owner_capability_lookup_digest(
            account_id,
            peer_id,
            membership_capability,
        )
    except ValueError:
        return False
    _ensure_db_initialized()
    with _db() as conn:
        row = conn.execute(
            """SELECT id, owner_membership_capability_hash, public_media_e2ee_version
               FROM sessions
               WHERE owner_capability_lookup_hash = ?
                 AND owner_account_id = ?
                 AND owner_peer_id = ?
                 AND revoked_at IS NULL
                 AND expires_at > ?
               LIMIT 1""",
            (lookup_hash, account_id, peer_id, _now()),
        ).fetchone()
        if not row:
            return False
        if int(row["public_media_e2ee_version"] or 0) != public_media_e2ee_version:
            return False
        expected_hash = membership_capability_digest(
            str(row["id"]),
            account_id,
            peer_id,
            membership_capability,
        )
        return secrets.compare_digest(
            str(row["owner_membership_capability_hash"] or ""),
            expected_hash,
        )


def is_join_recovery(
    *,
    invite_code: str,
    account_id: str,
    device_fingerprint: str,
    membership_capability: str,
    public_media_e2ee_version: int = 0,
) -> bool:
    """Recognize an already committed v2 join using its original capability."""
    try:
        peer_id = canonical_device_peer_id(account_id, device_fingerprint)
    except ValueError:
        return False
    _ensure_db_initialized()
    with _db() as conn:
        row = conn.execute(
            """SELECT p.session_id, p.membership_capability_hash,
                      p.public_media_e2ee_version
               FROM participants p
               JOIN sessions s ON s.id = p.session_id
               WHERE s.invite_code = ?
                 AND s.identity_binding_version = 2
                 AND s.revoked_at IS NULL
                 AND s.expires_at > ?
                 AND p.account_id = ?
                 AND p.peer_id = ?
                 AND p.revoked_at IS NULL
               LIMIT 1""",
            (str(invite_code or "").strip().upper(), _now(), account_id, peer_id),
        ).fetchone()
        if not row:
            return False
        if int(row["public_media_e2ee_version"] or 0) != public_media_e2ee_version:
            return False
        try:
            expected_hash = membership_capability_digest(
                str(row["session_id"]),
                account_id,
                peer_id,
                membership_capability,
            )
        except ValueError:
            return False
        return secrets.compare_digest(
            str(row["membership_capability_hash"] or ""),
            expected_hash,
        )


def join_session(
    *,
    invite_code: str,
    user_id: str,
    user_sub: str,
    device_id: str,
    device_fingerprint: str,
    public_key_spki_b64: str = "",
    oidc_issuer: str,
    expected_session_id: str = "",
    membership_capability: str = "",
    expected_identity_binding_version: int | None = None,
    public_media_e2ee_version: int | None = None,
    public_media_capabilities: Any = None,
) -> dict[str, Any]:
    _ensure_db_initialized()
    _cleanup_expired()
    code = str(invite_code or "").strip().upper()
    oidc_issuer = str(oidc_issuer or "").strip()
    user_sub = str(user_sub or "").strip()
    device_id = str(device_id or "").strip()
    device_fingerprint = str(device_fingerprint or "").strip().lower()
    public_key_spki_b64 = str(public_key_spki_b64 or "").strip()
    membership_capability = str(membership_capability or "").strip()
    negotiated_identity_binding_version = (
        1 if expected_identity_binding_version is None else expected_identity_binding_version
    )
    try:
        normalized_media_version = normalize_public_media_advertisement(
            public_media_e2ee_version,
            public_media_capabilities,
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    if normalized_media_version and negotiated_identity_binding_version != 2:
        return {"ok": False, "reason": "public_media_identity_binding_v2_required"}
    if not is_canonical_account_id(user_id) or not user_sub or not oidc_issuer:
        return {"ok": False, "reason": "oidc_identity_required"}
    if not device_id or not device_fingerprint or not public_key_spki_b64:
        return {"ok": False, "reason": "device_identity_required"}
    if not _is_protocol_identifier(device_id):
        return {"ok": False, "reason": "device_identity_invalid"}
    try:
        actual_fingerprint = spki_fingerprint(public_key_spki_b64)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    if not secrets.compare_digest(actual_fingerprint, device_fingerprint):
        return {"ok": False, "reason": "device_key_substitution"}

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM sessions WHERE invite_code = ? AND invite_code IS NOT NULL AND invite_code != ''",
            (code,),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "invalid_invite_code"}
        session = _row_to_session(row)
        sid = str(session.get("id") or "")
        if expected_session_id and sid != expected_session_id:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_revoked"}
        if float(session.get("expires_at") or 0) < _now():
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_expired"}
        sess_issuer = str(session.get("oidc_issuer") or "")
        if not sess_issuer or sess_issuer != oidc_issuer:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "oidc_issuer_mismatch"}
        identity_binding_version = int(session.get("identity_binding_version") or 0)
        if negotiated_identity_binding_version != identity_binding_version:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "identity_binding_version_mismatch"}
        if (
            session.get("security_mode") != "strict_e2ee"
            or int(session.get("security_contract_version") or 0) != 1
            or identity_binding_version not in {1, 2}
        ):
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "strict_e2ee_required"}
        peer_id = canonical_device_peer_id(user_id, device_fingerprint) if identity_binding_version == 2 else user_id
        owner_account_id = str(session.get("owner_account_id") or session.get("owner_user_id") or "")
        owner_peer_id = str(session.get("owner_peer_id") or session.get("owner_user_id") or "")
        if identity_binding_version == 1 and user_id == owner_account_id:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "peer_identity_must_be_distinct"}
        if secrets.compare_digest(device_fingerprint, str(session.get("owner_device_fingerprint") or "")):
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "device_key_must_be_distinct"}
        if peer_id == owner_peer_id:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "peer_identity_must_be_distinct"}

        existing = conn.execute(
            """
            SELECT id, session_id, user_id, user_sub_hash, device_id,
                   device_fingerprint, public_key_spki_b64, permissions,
                   joined_at, last_seen, revoked_at, account_id, peer_id,
                   membership_capability_hash, public_media_e2ee_version
            FROM participants
            WHERE session_id = ?
              AND ((? = 2 AND peer_id = ?) OR (? = 1 AND user_id = ? AND device_id = ?))
              AND revoked_at IS NULL
            LIMIT 1
            """,
            (sid, identity_binding_version, peer_id, identity_binding_version, user_id, device_id),
        ).fetchone()
        if existing:
            expected_sub_hash = _subject_hash(oidc_issuer, user_sub)
            if (
                not secrets.compare_digest(str(existing["user_sub_hash"]), expected_sub_hash)
                or not secrets.compare_digest(str(existing["account_id"] or existing["user_id"]), user_id)
                or not secrets.compare_digest(str(existing["peer_id"] or existing["user_id"]), peer_id)
                or not secrets.compare_digest(str(existing["device_id"]), device_id)
                or not secrets.compare_digest(str(existing["device_fingerprint"]), device_fingerprint)
                or not secrets.compare_digest(str(existing["public_key_spki_b64"]), public_key_spki_b64)
            ):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "device_identity_conflict"}
            if identity_binding_version == 2:
                if not membership_capability:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "membership_capability_required"}
                if not is_membership_capability(membership_capability):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "membership_capability_invalid"}
                expected_capability_hash = membership_capability_digest(
                    sid,
                    user_id,
                    peer_id,
                    membership_capability,
                )
                if not secrets.compare_digest(
                    str(existing["membership_capability_hash"] or ""),
                    expected_capability_hash,
                ):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "membership_capability_invalid"}
            if int(existing["public_media_e2ee_version"] or 0) != normalized_media_version:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "public_media_capability_conflict"}
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
                "public_key_spki_b64": existing["public_key_spki_b64"],
                "account_id": existing["account_id"] or existing["user_id"],
                "peer_id": existing["peer_id"] or existing["user_id"],
                "public_media_e2ee_version": int(existing["public_media_e2ee_version"] or 0),
                "public_media_capabilities": (
                    public_media_capabilities_v1()
                    if int(existing["public_media_e2ee_version"] or 0) == PUBLIC_MEDIA_E2EE_VERSION
                    else None
                ),
            }
            conn.execute("COMMIT")
            return {
                "ok": True,
                "participant": participant,
                "session": _session_snapshot(conn, session),
                "idempotent": True,
            }

        active_count = conn.execute(
            "SELECT COUNT(1) AS c FROM participants WHERE session_id = ? AND revoked_at IS NULL",
            (sid,),
        ).fetchone()
        if int(active_count["c"] or 0) >= 1:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_full"}

        participant = {
            "id": str(uuid.uuid4()),
            "session_id": sid,
            "user_id": user_id,
            "user_sub_hash": _subject_hash(oidc_issuer, user_sub),
            "device_id": device_id,
            "device_fingerprint": device_fingerprint,
            "permissions": dict(session.get("allowed_permissions") or {}),
            "joined_at": _now(),
            "last_seen": _now(),
            "revoked_at": None,
            "public_key_spki_b64": public_key_spki_b64,
            "account_id": user_id,
            "peer_id": peer_id,
            "public_media_e2ee_version": normalized_media_version,
            "public_media_capabilities": (
                public_media_capabilities_v1() if normalized_media_version == PUBLIC_MEDIA_E2EE_VERSION else None
            ),
        }
        if identity_binding_version == 2:
            if not membership_capability:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "membership_capability_required"}
            if not is_membership_capability(membership_capability):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "membership_capability_invalid"}
        new_capability = membership_capability if identity_binding_version == 2 else ""
        capability_hash = membership_capability_digest(sid, user_id, peer_id, new_capability) if new_capability else ""
        conn.execute(
            """
            INSERT INTO participants (
                id, session_id, user_id, user_sub_hash, device_id, device_fingerprint,
                public_key_spki_b64, permissions, joined_at, last_seen, revoked_at,
                account_id, peer_id, membership_capability_hash,
                public_media_e2ee_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                participant["id"],
                participant["session_id"],
                participant["user_id"],
                participant["user_sub_hash"],
                participant["device_id"],
                participant["device_fingerprint"],
                participant["public_key_spki_b64"],
                json.dumps(participant["permissions"], separators=(",", ":")),
                participant["joined_at"],
                participant["last_seen"],
                participant["account_id"],
                participant["peer_id"],
                capability_hash,
                normalized_media_version,
            ),
        )
        conn.execute("UPDATE sessions SET security_epoch = security_epoch + 1 WHERE id = ?", (sid,))
        conn.execute("DELETE FROM key_confirmations WHERE session_id = ?", (sid,))
        conn.execute("COMMIT")
        updated = _get_session_by_id(conn, sid)
        return {
            "ok": True,
            "participant": dict(participant),
            "session": _session_snapshot(conn, updated or session),
        }


def update_session_permissions(
    *,
    session_id: str,
    actor_user_id: str,
    permissions: dict[str, bool],
    actor_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            return {"ok": False, "reason": "session_inactive"}
        try:
            _resolve_authenticated_membership(
                conn,
                session,
                account_id=actor_user_id,
                requested_peer_id=actor_peer_id,
                membership_capability=membership_capability,
                owner_required=True,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        # Permission snapshots are part of the strict E2EE authorization
        # context. Updating them without advancing the security epoch and
        # negotiating fresh peer keys would leave clients with conflicting
        # authority. Public rendezvous supports only strict sessions, so the
        # mutation stays fail-closed until a rekey-capable adapter exists.
        return {"ok": False, "reason": "permission_update_rekey_required"}


def get_participants(
    *,
    session_id: str,
    requester_user_id: str,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            return {"ok": False, "reason": "session_inactive"}
        try:
            requester = _resolve_authenticated_membership(
                conn,
                session,
                account_id=requester_user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}

        presence = [
            {
                "id": f"member:{session_id}:owner",
                "user_id": session.get("owner_user_id"),
                "account_id": session.get("owner_account_id") or session.get("owner_user_id"),
                "peer_id": (
                    session.get("owner_peer_id")
                    if int(session.get("identity_binding_version") or 0) == 2
                    else session.get("owner_user_id")
                ),
                "device_id": session.get("owner_device_id"),
                "device_fingerprint": session.get("owner_device_fingerprint"),
                "permissions": session.get("allowed_permissions"),
                "joined_at": session.get("created_at"),
                "last_seen": _now(),
                "last_seen_at": _now(),
                "revoked_at": session.get("revoked_at"),
                "public_media_e2ee_version": int(session.get("public_media_e2ee_version") or 0),
                "public_media_capabilities": session.get("public_media_capabilities"),
            }
        ] + [
            {
                "id": p.get("id"),
                "user_id": p.get("user_id"),
                "account_id": p.get("account_id") or p.get("user_id"),
                "peer_id": (
                    p.get("peer_id") if int(session.get("identity_binding_version") or 0) == 2 else p.get("user_id")
                ),
                "device_id": p.get("device_id"),
                "device_fingerprint": p.get("device_fingerprint"),
                "permissions": p.get("permissions"),
                "joined_at": p.get("joined_at"),
                "last_seen": p.get("last_seen"),
                "last_seen_at": p.get("last_seen"),
                "revoked_at": p.get("revoked_at"),
                "public_media_e2ee_version": int(p.get("public_media_e2ee_version") or 0),
                "public_media_capabilities": p.get("public_media_capabilities"),
            }
            for p in _list_participants(conn, session_id, include_revoked=True)
        ]
        return {"ok": True, "participants": presence, "local_peer_id": requester["peer_id"]}


def touch_participant(
    *,
    session_id: str,
    user_id: str,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        try:
            member = _resolve_authenticated_membership(
                conn,
                session,
                account_id=user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if member["owner"]:
            return {"ok": True, "local_peer_id": member["peer_id"]}
        if int(session.get("identity_binding_version") or 0) == 2:
            conn.execute(
                """UPDATE participants SET last_seen = ?
                   WHERE session_id = ? AND peer_id = ? AND revoked_at IS NULL""",
                (_now(), session_id, member["peer_id"]),
            )
        else:
            conn.execute(
                """UPDATE participants SET last_seen = ?
                   WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL""",
                (_now(), session_id, user_id),
            )
        return {"ok": True, "local_peer_id": member["peer_id"]}


def _memberships(conn: sqlite3.Connection, session: dict[str, Any]) -> list[dict[str, Any]]:
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    owner_account_id = str(session.get("owner_account_id") or session.get("owner_user_id") or "")
    owner_peer_id = (
        str(session.get("owner_peer_id") or "")
        if identity_binding_version == 2
        else str(session.get("owner_user_id") or "")
    )
    owner = {
        "membership_id": f"member:{session['id']}:owner",
        "membership_version": 1,
        "account_id": owner_account_id,
        "peer_id": owner_peer_id,
        "device_id": session["owner_device_id"],
        "fingerprint": session["owner_device_fingerprint"],
        "public_key_spki_b64": session["owner_public_key_spki_b64"],
        "_membership_capability_hash": session.get("_owner_membership_capability_hash") or "",
        "public_media_e2ee_version": int(session.get("public_media_e2ee_version") or 0),
        "owner": True,
    }
    members = [owner]
    for participant in _list_participants(conn, session["id"]):
        members.append(
            {
                "membership_id": f"member:{participant['id']}",
                "membership_version": 1,
                "account_id": participant.get("account_id") or participant["user_id"],
                "peer_id": (
                    participant.get("peer_id") or "" if identity_binding_version == 2 else participant["user_id"]
                ),
                "device_id": participant["device_id"],
                "fingerprint": participant["device_fingerprint"],
                "public_key_spki_b64": participant["public_key_spki_b64"],
                "_membership_capability_hash": participant.get("_membership_capability_hash") or "",
                "public_media_e2ee_version": int(participant.get("public_media_e2ee_version") or 0),
                "owner": False,
            }
        )
    return members


def _validated_strict_memberships(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    require_pair: bool,
) -> list[dict[str, Any]]:
    """Return current pair membership after validating its security binding."""
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    if (
        session.get("security_mode") != "strict_e2ee"
        or int(session.get("security_contract_version") or 0) != 1
        or identity_binding_version not in {1, 2}
    ):
        raise ValueError("strict_e2ee_required")
    members = _memberships(conn, session)
    expected_count = 2 if require_pair else None
    if expected_count is not None and len(members) != expected_count:
        raise ValueError("strict_pair_membership_incomplete")
    if len(members) > 2:
        raise ValueError("strict_pair_cardinality_exceeded")
    for member in members:
        account_id = str(member.get("account_id") or "")
        peer_id = str(member.get("peer_id") or "")
        if not is_canonical_account_id(account_id):
            raise ValueError("account_identity_binding_invalid")
        if identity_binding_version == 1:
            if not _is_canonical_peer_id(peer_id) or peer_id != account_id:
                raise ValueError("peer_identity_binding_invalid")
        else:
            if not is_device_peer_id(peer_id):
                raise ValueError("peer_identity_binding_invalid")
            expected_peer_id = canonical_device_peer_id(
                account_id,
                str(member.get("fingerprint") or ""),
            )
            if not secrets.compare_digest(peer_id, expected_peer_id):
                raise ValueError("peer_identity_binding_invalid")
            capability_hash = str(member.get("_membership_capability_hash") or "")
            if len(capability_hash) != 64 or any(char not in "0123456789abcdef" for char in capability_hash):
                raise ValueError("membership_capability_binding_invalid")
        public_key = str(member.get("public_key_spki_b64") or "")
        fingerprint = str(member.get("fingerprint") or "").lower()
        try:
            actual_fingerprint = spki_fingerprint(public_key)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not secrets.compare_digest(actual_fingerprint, fingerprint):
            raise ValueError("device_key_substitution")
    if len(members) == 2:
        owner, guest = members
        if secrets.compare_digest(str(owner["peer_id"]), str(guest["peer_id"])):
            raise ValueError("peer_identity_must_be_distinct")
        if secrets.compare_digest(str(owner["fingerprint"]), str(guest["fingerprint"])):
            raise ValueError("device_key_must_be_distinct")
    return members


def _resolve_authenticated_membership(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    account_id: str,
    requested_peer_id: str = "",
    membership_capability: str = "",
    owner_required: bool = False,
) -> dict[str, Any]:
    """Resolve one session membership without trusting a client peer selector."""
    members = _validated_strict_memberships(conn, session, require_pair=False)
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    account_members = [member for member in members if member["account_id"] == account_id]
    if not account_members:
        raise ValueError("forbidden")
    requested_peer_id = str(requested_peer_id or "").strip()
    if identity_binding_version == 1:
        member = next(
            (candidate for candidate in account_members if candidate["peer_id"] == account_id),
            None,
        )
        if member is None or (requested_peer_id and requested_peer_id != member["peer_id"]):
            raise ValueError("forbidden")
    else:
        if not requested_peer_id:
            raise ValueError("local_peer_id_required")
        member = next(
            (candidate for candidate in account_members if candidate["peer_id"] == requested_peer_id),
            None,
        )
        if member is None:
            raise ValueError("forbidden")
        if not membership_capability:
            raise ValueError("membership_capability_required")
        if not is_membership_capability(membership_capability):
            raise ValueError("membership_capability_invalid")
        expected_hash = membership_capability_digest(
            str(session["id"]),
            account_id,
            requested_peer_id,
            membership_capability,
        )
        if not secrets.compare_digest(
            str(member.get("_membership_capability_hash") or ""),
            expected_hash,
        ):
            raise ValueError("membership_capability_invalid")
    if owner_required and not member["owner"]:
        raise ValueError("forbidden")
    return member


def authenticate_session_membership(
    *,
    session_id: str,
    account_id: str,
    requested_peer_id: str = "",
    membership_capability: str = "",
    require_pair: bool = False,
) -> dict[str, Any]:
    """Public application port for authentication before peer-scoped limits."""
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            return {"ok": False, "reason": "session_inactive"}
        try:
            if require_pair:
                _validated_strict_memberships(conn, session, require_pair=True)
            member = _resolve_authenticated_membership(
                conn,
                session,
                account_id=account_id,
                requested_peer_id=requested_peer_id,
                membership_capability=membership_capability,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        return {
            "ok": True,
            "local_peer_id": member["peer_id"],
            "identity_binding_version": int(session.get("identity_binding_version") or 0),
        }


def _strict_pair_material(
    conn: sqlite3.Connection,
    session: dict[str, Any],
) -> tuple[list[dict[str, Any]], PairSecurityAuthority, dict[str, Any]]:
    members = _validated_strict_memberships(conn, session, require_pair=True)
    authority = _SECURITY_AUTHORITY
    owner = next(member for member in members if member["owner"])
    guest = next(member for member in members if not member["owner"])
    return members, authority, authority.contract(session, owner, guest)


def _public_media_contract_v1(
    *,
    session: dict[str, Any],
    members: list[dict[str, Any]],
    authority: PairSecurityAuthority,
    base_contract: dict[str, Any],
) -> dict[str, Any] | None:
    """Negotiate media only for an exact v2 pair with two v1 adverts."""
    if int(session.get("identity_binding_version") or 0) != 2 or len(members) != 2:
        return None
    if any(int(member.get("public_media_e2ee_version") or 0) != PUBLIC_MEDIA_E2EE_VERSION for member in members):
        return None
    owner = next(member for member in members if member["owner"])
    guest = next(member for member in members if not member["owner"])
    return authority.public_media_contract(
        session=session,
        owner=owner,
        guest=guest,
        base_security_contract_digest=str(base_contract["digest"]),
    )


def _expected_key_package(
    *,
    members: list[dict[str, Any]],
    authority: PairSecurityAuthority,
    session: dict[str, Any],
    contract: dict[str, Any],
    sender_peer_id: str,
    recipient_peer_id: str,
) -> dict[str, Any]:
    """Return the package a sender must have received for this direction."""
    sender = next((member for member in members if member["peer_id"] == sender_peer_id), None)
    recipient = next((member for member in members if member["peer_id"] == recipient_peer_id), None)
    if sender is None or recipient is None or sender is recipient:
        raise ValueError("forbidden")
    return authority.key_package(
        session=session,
        membership=recipient,
        recipient_peer_id=sender["peer_id"],
        contract_digest=str(contract["digest"]),
    )


def get_key_packages(
    *,
    session_id: str,
    requester_user_id: str,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            return {"ok": False, "reason": "session_inactive"}
        try:
            members = _validated_strict_memberships(conn, session, require_pair=False)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        try:
            requester = _resolve_authenticated_membership(
                conn,
                session,
                account_id=requester_user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if len(members) == 1:
            authority = _SECURITY_AUTHORITY
            return {
                "ok": True,
                "epoch": session["security_epoch"],
                "tenant_id": TENANT_ID,
                "security_contract_digest": None,
                "security_contract": None,
                "public_media_security_contract_v1": None,
                "hub_key_id": authority.key_id,
                "hub_public_key_b64": authority.public_key_b64,
                "packages": [],
                "local_membership_id": requester["membership_id"],
                "local_peer_id": requester["peer_id"],
                "local_package_id": None,
            }
        try:
            members, authority, contract = _strict_pair_material(conn, session)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        requester = next(member for member in members if member["peer_id"] == requester["peer_id"])
        remote = next(member for member in members if member["peer_id"] != requester["peer_id"])
        package = authority.key_package(
            session=session,
            membership=remote,
            recipient_peer_id=requester["peer_id"],
            contract_digest=contract["digest"],
        )
        local_package = _expected_key_package(
            members=members,
            authority=authority,
            session=session,
            contract=contract,
            sender_peer_id=remote["peer_id"],
            recipient_peer_id=requester["peer_id"],
        )
        public_media_contract = _public_media_contract_v1(
            session=session,
            members=members,
            authority=authority,
            base_contract=contract,
        )
        return {
            "ok": True,
            "epoch": session["security_epoch"],
            "tenant_id": TENANT_ID,
            "security_contract_digest": contract["digest"],
            "security_contract": contract,
            "public_media_security_contract_v1": public_media_contract,
            "hub_key_id": authority.key_id,
            "hub_public_key_b64": authority.public_key_b64,
            "packages": [package],
            "local_membership_id": requester["membership_id"],
            "local_peer_id": requester["peer_id"],
            "local_package_id": local_package["package_id"],
        }


def put_key_confirmation(
    *,
    session_id: str,
    sender_peer_id: str,
    recipient_peer_id: str,
    package_id: str,
    epoch: int,
    confirmation_tag: str,
    sender_account_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    package_id = str(package_id or "").strip().lower()
    confirmation_tag = str(confirmation_tag or "").strip()
    try:
        raw_tag = base64.b64decode(confirmation_tag, validate=True)
    except (ValueError, TypeError):
        return {"ok": False, "reason": "confirmation_tag_invalid"}
    if len(raw_tag) != 32 or len(package_id) != 64 or any(char not in "0123456789abcdef" for char in package_id):
        return {"ok": False, "reason": "confirmation_invalid"}

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = _get_session_by_id(conn, session_id)
        if not session:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_inactive"}
        try:
            members, authority, contract = _strict_pair_material(conn, session)
            sender = _resolve_authenticated_membership(
                conn,
                session,
                account_id=sender_account_id or sender_peer_id,
                requested_peer_id=sender_peer_id,
                membership_capability=membership_capability,
            )
            if sender["peer_id"] != sender_peer_id:
                raise ValueError("forbidden")
            expected_package = _expected_key_package(
                members=members,
                authority=authority,
                session=session,
                contract=contract,
                sender_peer_id=sender_peer_id,
                recipient_peer_id=recipient_peer_id,
            )
        except ValueError as exc:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": str(exc)}
        if epoch != int(session["security_epoch"]):
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "epoch_mismatch"}
        if not secrets.compare_digest(str(expected_package["package_id"]), package_id):
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "key_package_binding_mismatch"}

        now = _now()
        existing = conn.execute(
            """SELECT package_id, confirmation_tag, created_at, expires_at
               FROM key_confirmations
               WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
            (session_id, epoch, sender_peer_id, recipient_peer_id),
        ).fetchone()
        if existing and float(existing["expires_at"] or 0) > now:
            if not (
                secrets.compare_digest(str(existing["package_id"]), package_id)
                and secrets.compare_digest(str(existing["confirmation_tag"]), confirmation_tag)
            ):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "key_confirmation_conflict"}
            conn.execute("COMMIT")
            return {
                "ok": True,
                "idempotent": True,
                "created_at_ms": int(float(existing["created_at"]) * 1000),
                "expires_at_ms": int(float(existing["expires_at"]) * 1000),
            }
        if existing:
            conn.execute(
                """DELETE FROM key_confirmations
                   WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
                (session_id, epoch, sender_peer_id, recipient_peer_id),
            )
        expires_at = min(float(session["expires_at"]), now + _KEY_CONFIRMATION_TTL_SECONDS)
        conn.execute(
            """
            INSERT INTO key_confirmations (
                session_id, epoch, sender_peer_id, recipient_peer_id, package_id,
                confirmation_tag, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                epoch,
                sender_peer_id,
                recipient_peer_id,
                package_id,
                confirmation_tag,
                now,
                expires_at,
            ),
        )
        conn.execute("COMMIT")
        log.info(
            "pair_key_confirmation_stored session=%s epoch=%d direction=%s",
            session_id,
            epoch,
            hashlib.sha256(f"{sender_peer_id}>{recipient_peer_id}".encode()).hexdigest()[:12],
        )
        return {
            "ok": True,
            "created_at_ms": int(now * 1000),
            "expires_at_ms": int(expires_at * 1000),
        }


def get_key_confirmation(
    *,
    session_id: str,
    requester_user_id: str,
    sender_peer_id: str,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = _get_session_by_id(conn, session_id)
        if not session:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "session_inactive"}
        try:
            members, authority, contract = _strict_pair_material(conn, session)
            requester = _resolve_authenticated_membership(
                conn,
                session,
                account_id=requester_user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
            expected_package = _expected_key_package(
                members=members,
                authority=authority,
                session=session,
                contract=contract,
                sender_peer_id=sender_peer_id,
                recipient_peer_id=requester["peer_id"],
            )
        except ValueError as exc:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": str(exc)}
        row = conn.execute(
            """SELECT confirmation_tag, package_id, epoch, created_at, expires_at
               FROM key_confirmations
               WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
            (session_id, session["security_epoch"], sender_peer_id, requester["peer_id"]),
        ).fetchone()
        now = _now()
        if row and float(row["expires_at"] or 0) <= now:
            conn.execute(
                """DELETE FROM key_confirmations
                   WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
                (session_id, session["security_epoch"], sender_peer_id, requester["peer_id"]),
            )
            row = None
        if row and not secrets.compare_digest(str(row["package_id"]), str(expected_package["package_id"])):
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "key_confirmation_binding_invalid"}
        conn.execute("COMMIT")
        confirmation = (
            None
            if row is None
            else {
                "confirmation_tag": row["confirmation_tag"],
                "package_id": row["package_id"],
                "epoch": row["epoch"],
                "created_at_ms": int(float(row["created_at"]) * 1000),
                "expires_at_ms": int(float(row["expires_at"]) * 1000),
            }
        )
        return {"ok": True, "confirmation": confirmation}


def revoke_session(
    *,
    session_id: str,
    actor_user_id: str,
    actor_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        try:
            actor = _resolve_authenticated_membership(
                conn,
                session,
                account_id=actor_user_id,
                requested_peer_id=actor_peer_id,
                membership_capability=membership_capability,
                owner_required=True,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        conn.execute("BEGIN IMMEDIATE")
        capability_lookup_hash = str(session.get("_owner_capability_lookup_hash") or "")
        if capability_lookup_hash:
            conn.execute(
                """INSERT OR IGNORE INTO retired_owner_capabilities (
                       owner_capability_lookup_hash, retired_at
                   ) VALUES (?, ?)""",
                (capability_lookup_hash, _now()),
            )
        conn.execute(
            "UPDATE sessions SET revoked_at = ?, invite_code = '' WHERE id = ?",
            (_now(), session_id),
        )
        conn.execute("COMMIT")
    return {"ok": True, "local_peer_id": actor["peer_id"]}


def is_authorized_participant(
    session_id: str,
    user_id: str,
    *,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> bool:
    _ensure_db_initialized()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if (
            not session
            or session.get("revoked_at") is not None
            or float(session.get("expires_at") or 0) <= _now()
            or session.get("security_mode") != "strict_e2ee"
            or int(session.get("security_contract_version") or 0) != 1
            or int(session.get("identity_binding_version") or 0) not in {1, 2}
        ):
            return False
        try:
            _resolve_authenticated_membership(
                conn,
                session,
                account_id=user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
        except ValueError:
            return False
        return True


# --- WebRTC signaling ---


def push_signal(
    *,
    session_id: str,
    sender_id: str,
    recipient_id: str,
    signal_type: str,
    payload: Any,
    sender_account_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    _ensure_db_initialized()
    if signal_type not in {"offer", "answer", "ice_candidate"}:
        return {"ok": False, "reason": "invalid_signal_type"}
    try:
        serialized_payload = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "signal_payload_invalid"}
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
        session = _get_session_by_id(conn, session_id)
        if not session or session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "forbidden"}
        try:
            members = _validated_strict_memberships(conn, session, require_pair=True)
            sender = _resolve_authenticated_membership(
                conn,
                session,
                account_id=sender_account_id or sender_id,
                requested_peer_id=sender_id,
                membership_capability=membership_capability,
            )
        except ValueError as exc:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": str(exc)}
        peer_ids = {str(member["peer_id"]) for member in members}
        if sender["peer_id"] != sender_id or sender_id not in peer_ids:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "forbidden"}
        if recipient_id not in peer_ids or recipient_id == sender_id:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "recipient_not_authorized"}
        queue_state = conn.execute(
            """SELECT COUNT(1) AS queue_size, COALESCE(MAX(sequence), 0) AS cursor
               FROM signals WHERE session_id = ? AND recipient_id = ?""",
            (session_id, recipient_id),
        ).fetchone()
        if int(queue_state["queue_size"] or 0) >= _MAX_SIGNAL_QUEUE:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "signal_queue_full"}
        cursor = int(queue_state["cursor"] or 0)
        if cursor >= _MAX_SIGNAL_CURSOR:
            conn.execute("ROLLBACK")
            return {"ok": False, "reason": "signal_sequence_exhausted"}
        sequence = cursor + 1
        entry["sequence"] = sequence
        conn.execute(
            """
            INSERT INTO signals (
                id, session_id, sender_id, recipient_id, signal_type, payload, sequence, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["id"],
                entry["session_id"],
                entry["sender_id"],
                entry["recipient_id"],
                entry["type"],
                serialized_payload,
                entry["sequence"],
                entry["sent_at"],
            ),
        )
        conn.execute("COMMIT")
    return {
        "ok": True,
        "signal_id": entry["id"],
        "sequence": str(entry["sequence"]),
        "cursor": str(entry["sequence"]),
    }


def poll_signals(
    *,
    session_id: str,
    user_id: str,
    since: int = 0,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    """Read signaling metadata without consuming it and expose retention gaps."""
    _ensure_db_initialized()
    if isinstance(since, bool) or not isinstance(since, int) or since < 0 or since > _MAX_SIGNAL_CURSOR:
        return {"ok": False, "reason": "signal_cursor_invalid"}
    with _db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = _poll_signals_locked(
                conn,
                session_id=session_id,
                user_id=user_id,
                since=since,
                requester_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
            conn.execute("COMMIT")
            return result
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _poll_signals_locked(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    user_id: str,
    since: int,
    requester_peer_id: str,
    membership_capability: str,
) -> dict[str, Any]:
    """Read, acknowledge and update one recipient cursor under a writer lock."""
    session = _get_session_by_id(conn, session_id)
    if not session or session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= _now():
        return {"ok": False, "reason": "forbidden"}
    try:
        members = _validated_strict_memberships(conn, session, require_pair=True)
        requester = _resolve_authenticated_membership(
            conn,
            session,
            account_id=user_id,
            requested_peer_id=requester_peer_id,
            membership_capability=membership_capability,
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    local_peer_id = str(requester["peer_id"])
    if local_peer_id not in {str(member["peer_id"]) for member in members}:
        return {"ok": False, "reason": "forbidden"}

    bounds = conn.execute(
        """SELECT COALESCE(MIN(sequence), 0) AS oldest,
                  COALESCE(MAX(sequence), 0) AS newest
           FROM signals WHERE session_id = ? AND recipient_id = ?""",
        (session_id, local_peer_id),
    ).fetchone()
    oldest = int(bounds["oldest"] or 0)
    newest = int(bounds["newest"] or 0)
    if since > newest:
        return {"ok": False, "reason": "signal_cursor_ahead"}
    cursor_floor = max(0, oldest - 1) if oldest else 0
    truncated = since < cursor_floor
    rows = conn.execute(
        """
        SELECT id, session_id, sender_id, recipient_id, signal_type, payload, sequence, sent_at
        FROM signals
        WHERE session_id = ? AND recipient_id = ? AND sequence > ?
        ORDER BY sequence ASC
        """,
        (session_id, local_peer_id, since),
    ).fetchall()
    out = [
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "sender_id": row["sender_id"],
            "recipient_id": row["recipient_id"],
            "type": row["signal_type"],
            "payload": json.loads(row["payload"]),
            "sequence": str(row["sequence"]),
            "sent_at": row["sent_at"],
        }
        for row in rows
    ]
    # `since` acknowledges the previous successful response. Keep its last row
    # as a monotonic sequence sentinel and delete only older rows; the writer
    # lock keeps bounds, page and acknowledgement one indivisible operation.
    if since > 0:
        conn.execute(
            """DELETE FROM signals
               WHERE session_id = ? AND recipient_id = ? AND sequence < ?""",
            (session_id, local_peer_id, since),
        )
    if not requester["owner"]:
        identity_binding_version = int(session.get("identity_binding_version") or 0)
        identity_column = "peer_id" if identity_binding_version == 2 else "user_id"
        conn.execute(
            f"""UPDATE participants SET last_seen = ?
                WHERE session_id = ? AND {identity_column} = ? AND revoked_at IS NULL""",
            (_now(), session_id, local_peer_id),
        )
    return {
        "ok": True,
        "signals": out,
        "cursor": str(newest),
        "cursor_floor": str(cursor_floor),
        "truncated": truncated,
        "retention_limit": _MAX_SIGNAL_QUEUE,
        "local_peer_id": local_peer_id,
    }


def consume_signals(*, session_id: str, user_id: str) -> list[dict[str, Any]]:
    """Compatibility wrapper; reads the retained window without deleting it."""
    result = poll_signals(session_id=session_id, user_id=user_id, since=0)
    return list(result.get("signals") or []) if result.get("ok") else []


# --- TURN credentials (coturn REST API format) ---


def issue_turn_credentials(
    *,
    session_id: str,
    requester_user_id: str,
    requester_peer_id: str = "",
    membership_capability: str = "",
) -> dict[str, Any]:
    """Issue coturn REST credentials only for a current strict-pair member."""
    import base64
    import hmac

    _ensure_db_initialized()
    now = _now()
    with _db() as conn:
        session = _get_session_by_id(conn, session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= now:
            return {"ok": False, "reason": "session_inactive"}
        try:
            memberships = _validated_strict_memberships(conn, session, require_pair=False)
            requester = _resolve_authenticated_membership(
                conn,
                session,
                account_id=requester_user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        local_peer_id = str(requester["peer_id"])
        if local_peer_id not in {str(member["peer_id"]) for member in memberships}:
            return {"ok": False, "reason": "forbidden"}
        if len(memberships) != 2:
            return {"ok": False, "reason": "strict_pair_membership_incomplete"}

    secret = cfg.TURN_SHARED_SECRET
    if not secret or not cfg.TURN_URLS or cfg.TURN_TTL_SECONDS < 1:
        return {"ok": False, "reason": "turn_not_configured"}
    now_seconds = int(now)
    expiry = min(now_seconds + cfg.TURN_TTL_SECONDS, int(float(session["expires_at"])))
    ttl = expiry - now_seconds
    if ttl < 1:
        return {"ok": False, "reason": "session_inactive"}
    session_pseudonym = hmac.new(
        secret.encode(),
        b"ananta.turn.session.v1\0" + session_id.encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    peer_pseudonym = hmac.new(
        secret.encode(),
        b"ananta.turn.session-peer.v1\0" + session_id.encode() + b"\0" + local_peer_id.encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    username = f"{expiry}:session-{session_pseudonym}:peer-{peer_pseudonym}"
    key = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    password = base64.b64encode(key).decode()
    log.info(
        "turn_credentials_issued session=%s peer=%s expires_at=%d",
        session_pseudonym,
        peer_pseudonym,
        expiry,
    )
    return {
        "ok": True,
        "credentials": {
            "username": username,
            "password": password,
            "ttl": ttl,
            "expires_at": expiry,
            "uris": list(cfg.TURN_URLS),
            "session_id": session_id,
            "local_peer_id": local_peer_id,
        },
    }


_ensure_db_initialized()
