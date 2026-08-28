"""Immutable SQLite revision store for Hub-owned Scrum control loops."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.services.interprocess_file_transaction import InterProcessFileTransaction


class ScrumStateConflictError(RuntimeError):
    pass


class ScrumStateStorePort(Protocol):
    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None: ...

    def list(self, kind: str, *, scope_id: str | None = None) -> list[dict[str, Any]]: ...

    def append(
        self,
        kind: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]: ...


class ScrumStateStore:
    """Persist immutable entity revisions with optimistic concurrency."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM scrum_entity_revisions "
                "WHERE kind=? AND entity_id=? ORDER BY revision DESC LIMIT 1",
                (_token(kind, "kind"), _token(entity_id, "entity_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_revision(self, kind: str, entity_id: str, revision: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM scrum_entity_revisions WHERE kind=? AND entity_id=? AND revision=?",
                (_token(kind, "kind"), _token(entity_id, "entity_id"), int(revision)),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str, *, scope_id: str | None = None) -> list[dict[str, Any]]:
        normalized_kind = _token(kind, "kind")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM scrum_entity_revisions AS candidate "
                "WHERE kind=? AND revision=(SELECT MAX(revision) FROM scrum_entity_revisions "
                "WHERE kind=candidate.kind AND entity_id=candidate.entity_id) ORDER BY entity_id",
                (normalized_kind,),
            ).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return values if scope_id is None else [value for value in values if value.get("scope_id") == scope_id]

    def append(
        self,
        kind: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_kind = _token(kind, "kind")
        normalized_id = _token(entity_id, "entity_id")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(revision) FROM scrum_entity_revisions WHERE kind=? AND entity_id=?",
                (normalized_kind, normalized_id),
            ).fetchone()
            current = int(row[0] or 0)
            if current != int(expected_revision):
                raise ScrumStateConflictError("scrum_state_revision_conflict")
            revision = current + 1
            value = {
                **dict(payload),
                "entity_kind": normalized_kind,
                "entity_id": normalized_id,
                "revision": revision,
            }
            rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            connection.execute(
                "INSERT INTO scrum_entity_revisions(kind,entity_id,revision,payload_json) VALUES (?,?,?,?)",
                (normalized_kind, normalized_id, revision, rendered),
            )
        return value

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scrum_entity_revisions("
                "kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                "payload_json TEXT NOT NULL, PRIMARY KEY(kind,entity_id,revision))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


def _token(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256 or any(character in normalized for character in "\r\n\0"):
        raise ValueError(f"scrum_{field}_invalid")
    return normalized


__all__ = ["ScrumStateConflictError", "ScrumStateStore", "ScrumStateStorePort"]
