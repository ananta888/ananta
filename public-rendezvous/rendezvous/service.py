"""Rendezvous- und Signaling-Session-Verwaltung.

Persistenter Session-Store über SQLite (shared über mehrere Gunicorn-Worker).

This module is the composition root and public facade of the rendezvous
domain. It owns the SQLite connection, the one-time schema initialization,
rate limiting, cleanup and the clock; the use cases live in collaborators that
receive this infrastructure through one :class:`RendezvousRuntime` (SRP/DIP):

- ``rendezvous_session_admission``: create and join sessions
- ``rendezvous_session_catalog``: account-scoped session listings
- ``rendezvous_membership_operations``: authenticated membership operations
- ``rendezvous_key_exchange``: Pair key packages and key confirmations
- ``rendezvous_signal_relay``: WebRTC signaling queues
- ``rendezvous_turn_credentials``: coturn REST credentials

``_now`` stays the single clock seam: the runtime resolves it per call.
"""

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any

from pair_security import (  # noqa: F401 - re-exported for app.py and callers
    PairSecurityAuthority,
    normalize_public_media_advertisement,
)
from peer_identity import (  # noqa: F401 - re-exported for app.py and callers
    is_device_peer_id,
    is_membership_capability,
)
from rendezvous_key_exchange import PairKeyExchange
from rendezvous_membership_operations import SessionMembershipOperations
from rendezvous_records import PROTOCOL_IDENTIFIER as _PROTOCOL_IDENTIFIER  # noqa: F401 - compatibility
from rendezvous_runtime import RendezvousRuntime
from rendezvous_schema import backfill_signal_sequences as _backfill_signal_sequences
from rendezvous_schema import migrate_database
from rendezvous_session_admission import SessionAdmission
from rendezvous_session_catalog import SessionCatalog
from rendezvous_signal_relay import SignalRelay
from rendezvous_turn_credentials import TurnCredentialIssuer

import config as cfg

log = logging.getLogger(__name__)

_lock = threading.Lock()
_db_init_lock = threading.Lock()
_db_initialized = False

# Legacy-Kompatibilität für bestehende Tests/Imports; fachlicher Zustand liegt in SQLite.
_sessions: dict[str, dict[str, Any]] = {}
_participants: dict[str, list[dict[str, Any]]] = {}
_invite_codes: dict[str, str] = {}

_last_cleanup: float = 0.0

_KEY_CONFIRMATION_TTL_SECONDS = 5 * 60
_CAPABILITY_TOMBSTONE_TTL_SECONDS = max(cfg.SESSION_MAX_DURATION_SECONDS, 24 * 60 * 60)
_MAX_SIGNAL_QUEUE = 256
_MAX_SIGNAL_BYTES = 8 * 1024
_MAX_SIGNAL_CURSOR = (1 << 63) - 1
_SECURITY_AUTHORITY = PairSecurityAuthority(cfg.RENDEZVOUS_SECURITY_SIGNING_SECRET)
_SECURITY_AUTHORITY.require_key_id(cfg.RENDEZVOUS_EXPECTED_SIGNING_KEY_ID)


def _now() -> float:
    return time.time()


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
    migrate_database(conn, clock=_now, backfill_sequences=_backfill_signal_sequences)


# --- Rate limiting ---


def _rate_check_with_retry(
    namespace: str,
    subject: str,
    limit: int,
    window: int,
) -> tuple[bool, int]:
    """Consume one bucket slot or return the minimum whole-second backoff."""
    if limit <= 0:
        return True, 0
    _ensure_db_initialized()
    key = hashlib.sha256(
        b"ananta.public-rendezvous.rate-limit.v1\0"
        + namespace.encode("utf-8")
        + b"\0"
        + subject.encode("utf-8")
    ).hexdigest()
    now = _now()
    with _db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM rate_limit_events WHERE bucket_key = ? AND observed_at <= ?",
                (key, now - window),
            )
            rows = conn.execute(
                "SELECT observed_at FROM rate_limit_events WHERE bucket_key = ? ORDER BY observed_at",
                (key,),
            ).fetchall()
            if len(rows) >= limit:
                retry_after = max(1, math.ceil(window - (now - float(rows[0]["observed_at"]))))
                conn.execute("COMMIT")
                return False, retry_after
            conn.execute(
                "INSERT INTO rate_limit_events(bucket_key, observed_at) VALUES (?, ?)",
                (key, now),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return True, 0


def _rate_check(namespace: str, subject: str, limit: int, window: int) -> bool:
    """Compatibility facade for callers that do not expose HTTP backoff metadata."""
    allowed, _retry_after = _rate_check_with_retry(namespace, subject, limit, window)
    return allowed


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
        max_rate_window = max(
            cfg.RATE_JOIN_WINDOW,
            cfg.RATE_CREATE_WINDOW,
            cfg.RATE_RECOVERY_PROBE_WINDOW,
            cfg.RATE_MEMBERSHIP_PROBE_WINDOW,
            cfg.RATE_SIGNAL_WINDOW,
            cfg.RATE_SIGNAL_POLL_WINDOW,
            cfg.RATE_TURN_CREDENTIAL_WINDOW,
        )
        conn.execute(
            "DELETE FROM rate_limit_events WHERE observed_at <= ?",
            (now - max_rate_window,),
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
        conn.execute("DELETE FROM rate_limit_events")
        conn.execute("COMMIT")
    with _lock:
        _sessions.clear()
        _participants.clear()
        _invite_codes.clear()
    _last_cleanup = 0.0


def reset_rate_limits_for_tests() -> None:
    """Clear only shared limiter state between contract-test phases."""
    _ensure_db_initialized()
    with _db() as conn:
        conn.execute("DELETE FROM rate_limit_events")


# --- Composition ---


def _clock() -> float:
    """Resolve ``_now`` per call so it stays the single, replaceable clock seam."""
    return _now()


_RUNTIME = RendezvousRuntime(
    connect=_db,
    ensure_schema=_ensure_db_initialized,
    cleanup_expired=_cleanup_expired,
    clock=_clock,
    config=cfg,
    security_authority=_SECURITY_AUTHORITY,
    logger=log,
)
_SESSION_ADMISSION = SessionAdmission(runtime=_RUNTIME)
_SESSION_CATALOG = SessionCatalog(runtime=_RUNTIME)
_MEMBERSHIP_OPERATIONS = SessionMembershipOperations(runtime=_RUNTIME)
_KEY_EXCHANGE = PairKeyExchange(runtime=_RUNTIME, confirmation_ttl_seconds=_KEY_CONFIRMATION_TTL_SECONDS)
_SIGNAL_RELAY = SignalRelay(runtime=_RUNTIME, max_queue=_MAX_SIGNAL_QUEUE, max_cursor=_MAX_SIGNAL_CURSOR)
_TURN_CREDENTIALS = TurnCredentialIssuer(runtime=_RUNTIME)

# --- Session operations ---

create_session = _SESSION_ADMISSION.create_session
is_owner_create_recovery = _SESSION_ADMISSION.is_owner_create_recovery
is_join_recovery = _SESSION_ADMISSION.is_join_recovery
join_session = _SESSION_ADMISSION.join_session
list_sessions_for_user = _SESSION_CATALOG.list_sessions_for_user
list_sessions_for_membership_proofs = _SESSION_CATALOG.list_sessions_for_membership_proofs
update_session_permissions = _MEMBERSHIP_OPERATIONS.update_session_permissions
get_participants = _MEMBERSHIP_OPERATIONS.get_participants
touch_participant = _MEMBERSHIP_OPERATIONS.touch_participant
leave_session = _MEMBERSHIP_OPERATIONS.leave_session
authenticate_session_membership = _MEMBERSHIP_OPERATIONS.authenticate_session_membership
set_membership_runtime = _MEMBERSHIP_OPERATIONS.set_membership_runtime
revoke_session = _MEMBERSHIP_OPERATIONS.revoke_session
is_authorized_participant = _MEMBERSHIP_OPERATIONS.is_authorized_participant
get_key_packages = _KEY_EXCHANGE.get_key_packages
put_key_confirmation = _KEY_EXCHANGE.put_key_confirmation
get_key_confirmation = _KEY_EXCHANGE.get_key_confirmation

# --- WebRTC signaling ---

push_signal = _SIGNAL_RELAY.push_signal
poll_signals = _SIGNAL_RELAY.poll_signals
consume_signals = _SIGNAL_RELAY.consume_signals

# --- TURN credentials (coturn REST API format) ---

issue_turn_credentials = _TURN_CREDENTIALS.issue_turn_credentials


_ensure_db_initialized()
