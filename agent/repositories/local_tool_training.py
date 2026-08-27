"""Immutable SQLite authority for independent outcomes and training records."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping, Protocol

from ananta_contracts.local_tool_training import (
    IndependentToolOutcome,
    ToolInteractionTrainingRecord,
)


class LocalToolArgumentRedactionPort(Protocol):
    def sanitize_arguments(self, value: Mapping[str, Any]) -> dict[str, Any]: ...


class SqliteLocalToolTrainingRepository:
    def __init__(
        self,
        path: str | Path,
        *,
        redaction: LocalToolArgumentRedactionPort,
    ) -> None:
        self._path = Path(path)
        self._redaction = redaction
        self._lock = threading.RLock()
        self._initialize()

    def save_outcome(self, outcome: IndependentToolOutcome) -> None:
        sanitized = outcome.model_copy(
            update={
                "decision": outcome.decision.model_copy(
                    update={
                        "arguments": self._redaction.sanitize_arguments(outcome.decision.arguments),
                    }
                ),
            }
        )
        self._insert_immutable(
            "independent_outcomes",
            sanitized.interaction_id,
            sanitized.model_dump_json(),
        )

    def get_outcome(self, interaction_id: str) -> IndependentToolOutcome | None:
        payload = self._read("independent_outcomes", interaction_id)
        return IndependentToolOutcome.model_validate_json(payload) if payload is not None else None

    def append_record(self, record: ToolInteractionTrainingRecord) -> None:
        self._insert_immutable("training_records", record.interaction_id, record.model_dump_json())

    def records(self) -> tuple[ToolInteractionTrainingRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM training_records ORDER BY interaction_id").fetchall()
        return tuple(ToolInteractionTrainingRecord.model_validate_json(row[0]) for row in rows)

    def _insert_immutable(self, table: str, interaction_id: str, payload: str) -> None:
        canonical = json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT payload_json FROM {table} WHERE interaction_id = ?",  # noqa: S608 - internal allowlist
                (interaction_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == canonical:
                    return
                raise ValueError("local_tool_training_immutable_conflict")
            connection.execute(
                f"INSERT INTO {table}(interaction_id, payload_json) VALUES (?, ?)",  # noqa: S608
                (interaction_id, canonical),
            )

    def _read(self, table: str, interaction_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE interaction_id = ?",  # noqa: S608
                (interaction_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            for table in ("independent_outcomes", "training_records"):
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table}("  # noqa: S608
                    "interaction_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
                )

    def _connect(self):
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["LocalToolArgumentRedactionPort", "SqliteLocalToolTrainingRepository"]
