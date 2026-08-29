"""Durable Hub state for peer-overlay plans, membership and one-use tickets."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.peer_overlay import canonical_overlay_digest, require_overlay_id


class PeerOverlayStateConflict(RuntimeError):
    pass


class PeerOverlayStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM peer_overlay_revisions WHERE kind=? AND entity_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (require_overlay_id(kind, "kind"), require_overlay_id(entity_id, "entity_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str) -> list[dict[str, Any]]:
        normalized = require_overlay_id(kind, "kind")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM peer_overlay_revisions candidate WHERE kind=? "
                "AND revision=(SELECT MAX(revision) FROM peer_overlay_revisions "
                "WHERE kind=candidate.kind AND entity_id=candidate.entity_id) ORDER BY entity_id",
                (normalized,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append(
        self,
        kind: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_kind = require_overlay_id(kind, "kind")
        normalized_id = require_overlay_id(entity_id, "entity_id")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(revision) FROM peer_overlay_revisions WHERE kind=? AND entity_id=?",
                (normalized_kind, normalized_id),
            ).fetchone()
            current = int(row[0] or 0)
            if current != expected_revision:
                raise PeerOverlayStateConflict("peer_overlay_revision_conflict")
            value = {
                **dict(payload),
                "entity_kind": normalized_kind,
                "entity_id": normalized_id,
                "revision": current + 1,
            }
            connection.execute(
                "INSERT INTO peer_overlay_revisions(kind,entity_id,revision,payload_json) VALUES (?,?,?,?)",
                (normalized_kind, normalized_id, current + 1, _render(value)),
            )
        return value

    def consume_ticket(self, ticket_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = require_overlay_id(ticket_id, "ticket_id")
        value = dict(payload)
        digest = canonical_overlay_digest(value)
        with self._transaction, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_digest FROM peer_overlay_consumed_tickets WHERE ticket_id=?",
                (normalized,),
            ).fetchone()
            if existing:
                raise PeerOverlayStateConflict("peer_overlay_ticket_replayed")
            connection.execute(
                "INSERT INTO peer_overlay_consumed_tickets(ticket_id,payload_digest,payload_json) VALUES (?,?,?)",
                (normalized, digest, _render(value)),
            )
        return value

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS peer_overlay_revisions("
                "kind TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,"
                "PRIMARY KEY(kind,entity_id,revision))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS peer_overlay_consumed_tickets("
                "ticket_id TEXT PRIMARY KEY,payload_digest TEXT NOT NULL,payload_json TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


def _render(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = ["PeerOverlayStateConflict", "PeerOverlayStateStore"]
