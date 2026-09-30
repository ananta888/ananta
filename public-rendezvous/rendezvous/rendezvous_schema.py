"""SQLite schema creation and additive migrations of the public rendezvous store.

``service`` owns the connection and the one-time initialization; this module
only knows the schema and its data backfills (SRP). The clock and the signal
cursor backfill are passed in so the owner keeps both seams.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from rendezvous_membership import invalidate_pair_runtime_locked


def migrate_database(
    conn: sqlite3.Connection,
    *,
    clock: Callable[[], float],
    backfill_sequences: Callable[[sqlite3.Connection], None],
) -> None:
    """Apply schema and cursor migration under one database-wide writer lock.

    ``backfill_sequences`` is normally :func:`backfill_signal_sequences`; the
    owner passes it explicitly so migration failures can be injected in tests.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_base_schema(conn)
        add_column(conn, "sessions", "owner_device_id TEXT NOT NULL DEFAULT 'owner-device'")
        add_column(conn, "sessions", "owner_public_key_spki_b64 TEXT NOT NULL DEFAULT ''")
        add_column(conn, "sessions", "security_epoch INTEGER NOT NULL DEFAULT 1")
        add_column(conn, "sessions", "security_mode TEXT NOT NULL DEFAULT 'legacy'")
        add_column(conn, "sessions", "security_contract_version INTEGER NOT NULL DEFAULT 0")
        add_column(conn, "sessions", "identity_binding_version INTEGER NOT NULL DEFAULT 0")
        add_column(conn, "sessions", "owner_account_id TEXT NOT NULL DEFAULT ''")
        add_column(conn, "sessions", "owner_peer_id TEXT NOT NULL DEFAULT ''")
        add_column(conn, "sessions", "owner_membership_capability_hash TEXT NOT NULL DEFAULT ''")
        add_column(conn, "sessions", "owner_capability_lookup_hash TEXT NOT NULL DEFAULT ''")
        add_column(conn, "sessions", "owner_create_request_hash TEXT NOT NULL DEFAULT ''")
        add_column(conn, "sessions", "public_media_e2ee_version INTEGER NOT NULL DEFAULT 0")
        add_column(conn, "sessions", "owner_runtime_state TEXT NOT NULL DEFAULT 'active'")
        add_column(conn, "participants", "public_key_spki_b64 TEXT NOT NULL DEFAULT ''")
        add_column(conn, "participants", "account_id TEXT NOT NULL DEFAULT ''")
        add_column(conn, "participants", "peer_id TEXT NOT NULL DEFAULT ''")
        add_column(conn, "participants", "membership_capability_hash TEXT NOT NULL DEFAULT ''")
        add_column(conn, "participants", "public_media_e2ee_version INTEGER NOT NULL DEFAULT 0")
        add_column(conn, "participants", "runtime_state TEXT NOT NULL DEFAULT 'active'")
        add_column(conn, "signals", "sequence INTEGER NOT NULL DEFAULT 0")
        signal_epoch_added = add_column(
            conn,
            "signals",
            "security_epoch INTEGER NOT NULL DEFAULT 0",
        )
        add_column(conn, "key_confirmations", "expires_at REAL NOT NULL DEFAULT 0")
        backfill_v1_identity_columns(conn)
        if signal_epoch_added:
            backfill_signal_epochs(conn)
        reconcile_v2_runtime_memberships(conn, clock=clock)
        backfill_sequences(conn)
        # Compatibility boundary: the pre-cursor image writes the column
        # default (0). A persistent unique index would make rollback fail on
        # its second signal. New writers serialize allocation with BEGIN
        # IMMEDIATE, so the index is unnecessary.
        conn.execute("DROP INDEX IF EXISTS idx_signals_recipient_sequence")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner_account ON sessions(owner_account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_participants_account ON participants(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_participants_peer ON participants(session_id, peer_id)")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_signals_recipient_epoch
               ON signals(session_id, recipient_id, security_epoch, sequence)"""
        )
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


def create_base_schema(conn: sqlite3.Connection) -> None:
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
               security_epoch INTEGER NOT NULL DEFAULT 0,
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
        """CREATE TABLE IF NOT EXISTS rate_limit_events (
               bucket_key TEXT NOT NULL,
               observed_at REAL NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_rate_limit_bucket ON rate_limit_events(bucket_key, observed_at)",
        "CREATE INDEX IF NOT EXISTS idx_rate_limit_expiry ON rate_limit_events(observed_at)",
    )
    for statement in statements:
        conn.execute(statement)


def add_column(conn: sqlite3.Connection, table: str, definition: str) -> bool:
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
        return True
    return False


def backfill_signal_sequences(conn: sqlite3.Connection) -> None:
    """Assign deterministic per-recipient cursors to rows from the legacy schema."""
    rows = conn.execute(
        """SELECT rowid, session_id, recipient_id, security_epoch, sequence
           FROM signals
           ORDER BY session_id, recipient_id, security_epoch, sent_at, rowid"""
    ).fetchall()
    partition: tuple[str, str, int] | None = None
    high_watermark = 0
    for row in rows:
        current_partition = (
            str(row["session_id"]),
            str(row["recipient_id"]),
            int(row["security_epoch"] or 0),
        )
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


def backfill_signal_epochs(conn: sqlite3.Connection) -> None:
    """Bind pre-epoch signaling rows to the session epoch seen at migration."""
    conn.execute(
        """UPDATE signals
           SET security_epoch = COALESCE(
               (SELECT security_epoch FROM sessions WHERE sessions.id = signals.session_id),
               security_epoch
           )
           WHERE security_epoch = 0"""
    )


def reconcile_v2_runtime_memberships(conn: sqlite3.Connection, *, clock: Callable[[], float]) -> None:
    """Park duplicate pre-runtime device memberships, keeping the newest session."""
    now = clock()
    rows = conn.execute(
        """SELECT s.id AS session_id, s.owner_account_id AS account_id,
                  s.owner_peer_id AS peer_id, s.created_at AS session_created_at,
                  1 AS owner, '' AS participant_id
           FROM sessions s
           WHERE s.identity_binding_version = 2
             AND s.owner_runtime_state = 'active'
             AND s.owner_account_id != '' AND s.owner_peer_id != ''
             AND s.revoked_at IS NULL AND s.expires_at > ?
           UNION ALL
           SELECT s.id AS session_id, p.account_id AS account_id,
                  p.peer_id AS peer_id, s.created_at AS session_created_at,
                  0 AS owner, p.id AS participant_id
           FROM participants p
           JOIN sessions s ON s.id = p.session_id
           WHERE s.identity_binding_version = 2
             AND p.runtime_state = 'active'
             AND p.account_id != '' AND p.peer_id != ''
             AND p.revoked_at IS NULL
             AND s.revoked_at IS NULL AND s.expires_at > ?""",
        (now, now),
    ).fetchall()
    memberships_by_peer: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        key = (str(row["account_id"]), str(row["peer_id"]))
        memberships_by_peer.setdefault(key, []).append(row)

    invalidated_session_ids: set[str] = set()
    for memberships in memberships_by_peer.values():
        if len(memberships) < 2:
            continue
        ordered = sorted(
            memberships,
            key=lambda row: (
                float(row["session_created_at"] or 0),
                str(row["session_id"]),
            ),
            reverse=True,
        )
        for duplicate in ordered[1:]:
            session_id = str(duplicate["session_id"])
            if bool(duplicate["owner"]):
                conn.execute(
                    "UPDATE sessions SET owner_runtime_state = 'parked' WHERE id = ?",
                    (session_id,),
                )
            else:
                conn.execute(
                    """UPDATE participants SET runtime_state = 'parked'
                       WHERE id = ? AND session_id = ? AND revoked_at IS NULL""",
                    (str(duplicate["participant_id"]), session_id),
                )
            invalidated_session_ids.add(session_id)

    for session_id in sorted(invalidated_session_ids):
        invalidate_pair_runtime_locked(conn, session_id)


def backfill_v1_identity_columns(conn: sqlite3.Connection) -> None:
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


__all__ = [
    "add_column",
    "backfill_signal_epochs",
    "backfill_signal_sequences",
    "backfill_v1_identity_columns",
    "create_base_schema",
    "migrate_database",
    "reconcile_v2_runtime_memberships",
]
