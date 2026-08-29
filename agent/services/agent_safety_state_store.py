"""Durable immutable state for Hub-owned agent safety controls."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.agent_safety import canonical_digest, require_token


class AgentSafetyStateConflictError(RuntimeError):
    pass


class AgentSafetyStateStorePort(Protocol):
    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None: ...
    def list(self, kind: str, *, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def append(
        self, kind: str, entity_id: str, payload: Mapping[str, Any], *, expected_revision: int
    ) -> dict[str, Any]: ...
    def append_event(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def list_events(self, *, run_id: str, limit: int = 1000) -> list[dict[str, Any]]: ...


class AgentSafetyStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_safety_entity_revisions "
                "WHERE kind=? AND entity_id=? ORDER BY revision DESC LIMIT 1",
                (require_token(kind, "kind"), require_token(entity_id, "entity_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM agent_safety_entity_revisions AS candidate "
                "WHERE kind=? AND revision=(SELECT MAX(revision) FROM agent_safety_entity_revisions "
                "WHERE kind=candidate.kind AND entity_id=candidate.entity_id) ORDER BY entity_id",
                (require_token(kind, "kind"),),
            ).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return values if run_id is None else [item for item in values if item.get("run_id") == run_id]

    def append(
        self,
        kind: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_kind = require_token(kind, "kind")
        normalized_id = require_token(entity_id, "entity_id")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(revision) FROM agent_safety_entity_revisions WHERE kind=? AND entity_id=?",
                (normalized_kind, normalized_id),
            ).fetchone()
            current = int(row[0] or 0)
            if current != int(expected_revision):
                raise AgentSafetyStateConflictError("agent_safety_state_revision_conflict")
            value = {
                **dict(payload),
                "entity_kind": normalized_kind,
                "entity_id": normalized_id,
                "revision": current + 1,
            }
            connection.execute(
                "INSERT INTO agent_safety_entity_revisions(kind,entity_id,revision,payload_json) VALUES (?,?,?,?)",
                (normalized_kind, normalized_id, current + 1, _render(value)),
            )
        return value

    def append_event(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        event_id = require_token(value.get("event_id"), "event_id")
        digest = canonical_digest(value)
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_digest,payload_json FROM agent_safety_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row:
                if not hmac_compare(str(row[0]), digest):
                    raise AgentSafetyStateConflictError("agent_safety_event_idempotency_conflict")
                return json.loads(row[1])
            connection.execute(
                "INSERT INTO agent_safety_events(event_id,run_id,payload_digest,payload_json) VALUES (?,?,?,?)",
                (event_id, require_token(value.get("run_id"), "run_id"), digest, _render(value)),
            )
        return value

    def list_events(self, *, run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        bounded = min(max(int(limit), 1), 10_000)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM agent_safety_events WHERE run_id=? ORDER BY sequence LIMIT ?",
                (require_token(run_id, "run_id"), bounded),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agent_safety_entity_revisions("
                "kind TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,"
                "PRIMARY KEY(kind,entity_id,revision))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agent_safety_events("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,run_id TEXT NOT NULL,"
                "payload_digest TEXT NOT NULL,payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_agent_safety_events_run ON agent_safety_events(run_id,sequence)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


def _render(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


__all__ = ["AgentSafetyStateConflictError", "AgentSafetyStateStore", "AgentSafetyStateStorePort"]
