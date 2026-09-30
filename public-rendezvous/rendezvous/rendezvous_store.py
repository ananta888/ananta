"""SQLite store of the public rendezvous service.

:class:`RendezvousStore` owns the database connection, the one-time schema
migration, the shared rate limiter and the expiry cleanup of one service
instance. Its clock and the signal-cursor backfill used by the migration are
injected (DIP) instead of being looked up on a module at call time, so tests
pass doubles to the constructor rather than patching module attributes.
"""

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from rendezvous_schema import migrate_database

Clock = Callable[[], float]
SequenceBackfill = Callable[[sqlite3.Connection], None]


class RendezvousStore:
    """Connections, schema initialization, rate limiting and cleanup for one database."""

    def __init__(
        self,
        *,
        config: Any,
        clock: Clock,
        backfill_sequences: SequenceBackfill,
        capability_tombstone_ttl_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._clock = clock
        self._backfill_sequences = backfill_sequences
        self._capability_tombstone_ttl_seconds = capability_tombstone_ttl_seconds
        self._log = logger
        self._init_lock = threading.Lock()
        self._initialized = False
        self._last_cleanup = 0.0

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection to the configured database (path resolved per call)."""
        conn = sqlite3.connect(
            self._config.RENDEZVOUS_DB_PATH,
            timeout=self._config.RENDEZVOUS_DB_TIMEOUT_SECONDS,
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

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self.connect() as conn:
                self.migrate(conn)
            self._initialized = True

    def migrate(self, conn: sqlite3.Connection) -> None:
        """Apply schema and cursor migration under one database-wide writer lock."""
        migrate_database(conn, clock=self._clock, backfill_sequences=self._backfill_sequences)

    # --- Rate limiting ---

    def rate_check_with_retry(self, namespace: str, subject: str, limit: int, window: int) -> tuple[bool, int]:
        """Consume one bucket slot or return the minimum whole-second backoff."""
        if limit <= 0:
            return True, 0
        self.ensure_initialized()
        key = hashlib.sha256(
            b"ananta.public-rendezvous.rate-limit.v1\0"
            + namespace.encode("utf-8")
            + b"\0"
            + subject.encode("utf-8")
        ).hexdigest()
        now = self._clock()
        with self.connect() as conn:
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

    def rate_check(self, namespace: str, subject: str, limit: int, window: int) -> bool:
        """Compatibility facade for callers that do not expose HTTP backoff metadata."""
        allowed, _retry_after = self.rate_check_with_retry(namespace, subject, limit, window)
        return allowed

    # --- Cleanup ---

    def _max_rate_window(self) -> int:
        cfg = self._config
        return max(
            cfg.RATE_JOIN_WINDOW,
            cfg.RATE_CREATE_WINDOW,
            cfg.RATE_RECOVERY_PROBE_WINDOW,
            cfg.RATE_MEMBERSHIP_PROBE_WINDOW,
            cfg.RATE_SIGNAL_WINDOW,
            cfg.RATE_SIGNAL_POLL_WINDOW,
            cfg.RATE_TURN_CREDENTIAL_WINDOW,
        )

    def cleanup_expired(self) -> None:
        now = self._clock()
        if now - self._last_cleanup < self._config.SESSION_CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup = now
        self.ensure_initialized()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM retired_owner_capabilities WHERE retired_at <= ?",
                (now - self._capability_tombstone_ttl_seconds,),
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
            conn.execute(
                "DELETE FROM rate_limit_events WHERE observed_at <= ?",
                (now - self._max_rate_window(),),
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
            self._log.info("Cleaned up %d expired/revoked sessions", deleted)

    # --- Test support ---

    def reset_for_tests(self) -> None:
        """Empty the persistent store for isolated tests."""
        self.ensure_initialized()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM signals")
            conn.execute("DELETE FROM key_confirmations")
            conn.execute("DELETE FROM participants")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM retired_owner_capabilities")
            conn.execute("DELETE FROM rate_limit_events")
            conn.execute("COMMIT")
        self._last_cleanup = 0.0

    def reset_rate_limits_for_tests(self) -> None:
        """Clear only shared limiter state between contract-test phases."""
        self.ensure_initialized()
        with self.connect() as conn:
            conn.execute("DELETE FROM rate_limit_events")


__all__ = ["Clock", "RendezvousStore", "SequenceBackfill"]
