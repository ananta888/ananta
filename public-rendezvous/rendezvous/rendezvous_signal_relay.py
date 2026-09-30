"""Bounded, epoch-fenced WebRTC signaling queues of strict Pair sessions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from rendezvous_membership import (
    require_transport_ready_locked,
    resolve_authenticated_membership,
    validated_strict_memberships,
)
from rendezvous_records import get_session_by_id
from rendezvous_runtime import RendezvousRuntime


class SignalRelay:
    """Queue, page and acknowledge signaling messages per recipient cursor."""

    def __init__(self, *, runtime: RendezvousRuntime, max_queue: int, max_cursor: int) -> None:
        self._runtime = runtime
        self._max_queue = max_queue
        self._max_cursor = max_cursor

    def push_signal(
        self,
        *,
        session_id: str,
        sender_id: str,
        recipient_id: str,
        signal_type: str,
        payload: Any,
        security_epoch: int | None = None,
        sender_account_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
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
            "sent_at": self._runtime.clock(),
        }
        with self._runtime.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = get_session_by_id(conn, session_id)
            if (
                not session
                or session.get("revoked_at") is not None
                or float(session.get("expires_at") or 0) <= self._runtime.clock()
            ):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "forbidden"}
            try:
                members = validated_strict_memberships(conn, session, require_pair=True)
                sender = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=sender_account_id or sender_id,
                    requested_peer_id=sender_id,
                    membership_capability=membership_capability,
                )
                current_epoch = int(session.get("security_epoch") or 0)
                if int(session.get("identity_binding_version") or 0) == 2:
                    if isinstance(security_epoch, bool) or not isinstance(security_epoch, int):
                        raise ValueError("signal_epoch_required")
                    if security_epoch != current_epoch:
                        raise ValueError("epoch_mismatch")
                elif security_epoch is not None and security_epoch != current_epoch:
                    raise ValueError("epoch_mismatch")
                require_transport_ready_locked(conn, session, members, clock=self._runtime.clock)
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
                   FROM signals
                   WHERE session_id = ? AND recipient_id = ? AND security_epoch = ?""",
                (session_id, recipient_id, current_epoch),
            ).fetchone()
            if int(queue_state["queue_size"] or 0) >= self._max_queue:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "signal_queue_full"}
            cursor = int(queue_state["cursor"] or 0)
            if cursor >= self._max_cursor:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "signal_sequence_exhausted"}
            sequence = cursor + 1
            entry["sequence"] = sequence
            entry["security_epoch"] = current_epoch
            conn.execute(
                """
                INSERT INTO signals (
                    id, session_id, sender_id, recipient_id, signal_type, payload,
                    sequence, security_epoch, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["id"],
                    entry["session_id"],
                    entry["sender_id"],
                    entry["recipient_id"],
                    entry["type"],
                    serialized_payload,
                    entry["sequence"],
                    entry["security_epoch"],
                    entry["sent_at"],
                ),
            )
            conn.execute("COMMIT")
        return {
            "ok": True,
            "signal_id": entry["id"],
            "sequence": str(entry["sequence"]),
            "cursor": str(entry["sequence"]),
            "security_epoch": entry["security_epoch"],
        }

    def poll_signals(
        self,
        *,
        session_id: str,
        user_id: str,
        since: int = 0,
        requester_peer_id: str = "",
        membership_capability: str = "",
        security_epoch: int | None = None,
    ) -> dict[str, Any]:
        """Read signaling metadata without consuming it and expose retention gaps."""
        self._runtime.ensure_schema()
        if isinstance(since, bool) or not isinstance(since, int) or since < 0 or since > self._max_cursor:
            return {"ok": False, "reason": "signal_cursor_invalid"}
        with self._runtime.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = self._poll_signals_locked(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                    since=since,
                    requester_peer_id=requester_peer_id,
                    membership_capability=membership_capability,
                    security_epoch=security_epoch,
                )
                conn.execute("COMMIT")
                return result
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _poll_signals_locked(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        user_id: str,
        since: int,
        requester_peer_id: str,
        membership_capability: str,
        security_epoch: int | None,
    ) -> dict[str, Any]:
        """Read, acknowledge and update one recipient cursor under a writer lock."""
        session = get_session_by_id(conn, session_id)
        if (
            not session
            or session.get("revoked_at") is not None
            or float(session.get("expires_at") or 0) <= self._runtime.clock()
        ):
            return {"ok": False, "reason": "forbidden"}
        try:
            members = validated_strict_memberships(conn, session, require_pair=True)
            requester = resolve_authenticated_membership(
                conn,
                session,
                account_id=user_id,
                requested_peer_id=requester_peer_id,
                membership_capability=membership_capability,
            )
            current_epoch = int(session.get("security_epoch") or 0)
            if int(session.get("identity_binding_version") or 0) == 2:
                if isinstance(security_epoch, bool) or not isinstance(security_epoch, int):
                    raise ValueError("signal_epoch_required")
                if security_epoch != current_epoch:
                    raise ValueError("epoch_mismatch")
            elif security_epoch is not None and security_epoch != current_epoch:
                raise ValueError("epoch_mismatch")
            require_transport_ready_locked(conn, session, members, clock=self._runtime.clock)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        local_peer_id = str(requester["peer_id"])
        if local_peer_id not in {str(member["peer_id"]) for member in members}:
            return {"ok": False, "reason": "forbidden"}

        bounds = conn.execute(
            """SELECT COALESCE(MIN(sequence), 0) AS oldest,
                      COALESCE(MAX(sequence), 0) AS newest
               FROM signals
               WHERE session_id = ? AND recipient_id = ? AND security_epoch = ?""",
            (session_id, local_peer_id, current_epoch),
        ).fetchone()
        oldest = int(bounds["oldest"] or 0)
        newest = int(bounds["newest"] or 0)
        if since > newest:
            return {"ok": False, "reason": "signal_cursor_ahead"}
        cursor_floor = max(0, oldest - 1) if oldest else 0
        truncated = since < cursor_floor
        rows = conn.execute(
            """
            SELECT id, session_id, sender_id, recipient_id, signal_type, payload,
                   sequence, security_epoch, sent_at
            FROM signals
            WHERE session_id = ? AND recipient_id = ? AND security_epoch = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (session_id, local_peer_id, current_epoch, since),
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
                "security_epoch": int(row["security_epoch"]),
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
                   WHERE session_id = ? AND recipient_id = ?
                     AND security_epoch = ? AND sequence < ?""",
                (session_id, local_peer_id, current_epoch, since),
            )
        if not requester["owner"]:
            identity_binding_version = int(session.get("identity_binding_version") or 0)
            identity_column = "peer_id" if identity_binding_version == 2 else "user_id"
            conn.execute(
                f"""UPDATE participants SET last_seen = ?
                    WHERE session_id = ? AND {identity_column} = ? AND revoked_at IS NULL""",
                (self._runtime.clock(), session_id, local_peer_id),
            )
        return {
            "ok": True,
            "signals": out,
            "cursor": str(newest),
            "cursor_floor": str(cursor_floor),
            "truncated": truncated,
            "retention_limit": self._max_queue,
            "local_peer_id": local_peer_id,
            "security_epoch": current_epoch,
        }

    def consume_signals(self, *, session_id: str, user_id: str) -> list[dict[str, Any]]:
        """Compatibility wrapper; reads the retained window without deleting it."""
        result = self.poll_signals(session_id=session_id, user_id=user_id, since=0)
        return list(result.get("signals") or []) if result.get("ok") else []


__all__ = ["SignalRelay"]
