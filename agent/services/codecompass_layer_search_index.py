"""SQLite FTS5 projection of one layer head's effective ``chunks`` view.

Scoring every chunk in Python on each query does not scale to a complete
repository (~100k chunks). The Hub keeps one FTS5 index per layer profile on
disk and follows the head incrementally: when the head only appended layers
to the chain the index already holds, just those layers are applied (upserts
replace, tombstones delete); any other change (new base, compaction,
rollback) rebuilds from the full chain. Queries return the best candidates
and the exact number of matching chunks.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[0-9A-Za-zÀ-ɏ]+")
_SCHEMA_VERSION = "1"


def fts_query(query: str) -> str:
    """OR of the query's word tokens, each quoted: no FTS syntax reaches SQLite."""
    tokens = list(dict.fromkeys(token.lower() for token in _TOKEN.findall(str(query or "")) if len(token) >= 2))
    return " OR ".join(f'"{token}"' for token in tokens[:32])


class LayerSearchIndex:
    """FTS5 index for one profile, kept in step with its layer head."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY, path TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
                start_line INTEGER, end_line INTEGER, content TEXT NOT NULL, content_hash TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                path, symbol, content, content='chunks', content_rowid='rowid', tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, path, symbol, content)
                VALUES (new.rowid, new.path, new.symbol, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, path, symbol, content)
                VALUES ('delete', old.rowid, old.path, old.symbol, old.content);
            END;
            """
        )
        return connection

    # --- keeping up with the head ------------------------------------------------------

    def state(self) -> dict[str, str]:
        with closing(self._connect()) as connection, connection:
            return dict(connection.execute("SELECT key, value FROM meta").fetchall())

    def sync(self, *, generation: int, chain: list[str], load_layer: Callable[[str], Mapping[str, Any] | None]) -> str:
        """Bring the index to ``chain``; returns ``current``, ``applied`` or ``rebuilt``."""
        with self._lock:
            state = self.state()
            known = [item for item in state.get("chain", "").split(",") if item]
            if state.get("version") == _SCHEMA_VERSION and known == chain:
                return "current"
            incremental = state.get("version") == _SCHEMA_VERSION and known and chain[: len(known)] == known
            pending = chain[len(known):] if incremental else chain
            with closing(self._connect()) as connection, connection:
                if not incremental:
                    connection.execute("DELETE FROM chunks")
                for layer_id in pending:
                    layer = load_layer(layer_id)
                    if layer is None:
                        raise LookupError(f"codecompass_layer_missing:{layer_id}")
                    self._apply(connection, layer.get("records") or [])
                connection.executemany(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    [("version", _SCHEMA_VERSION), ("generation", str(int(generation))), ("chain", ",".join(chain))],
                )
            return "applied" if incremental else "rebuilt"

    @staticmethod
    def _apply(connection: sqlite3.Connection, records: Iterable[Mapping[str, Any]]) -> None:
        for record in records:
            record_id = str(record.get("id") or "")
            if not record_id:
                continue
            connection.execute("DELETE FROM chunks WHERE id = ?", (record_id,))
            if record.get("tombstone") or record.get("operation") == "tombstone":
                continue
            connection.execute(
                "INSERT INTO chunks(id, path, symbol, kind, start_line, end_line, content, content_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    str(record.get("path") or ""),
                    str(record.get("symbol") or ""),
                    str(record.get("kind") or ""),
                    record.get("start_line"),
                    record.get("end_line"),
                    str(record.get("content") or ""),
                    str(record.get("content_hash") or ""),
                ),
            )

    # --- search -----------------------------------------------------------------------------

    def search(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        """Best ``limit`` chunks by BM25 (path and symbol weigh more) and the total match count."""
        match = fts_query(query)
        if not match:
            return [], 0
        with closing(self._connect()) as connection, connection:
            total = int(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                                           (match,)).fetchone()[0])
            rows = connection.execute(
                "SELECT c.id, c.path, c.symbol, c.kind, c.start_line, c.end_line, c.content, c.content_hash"
                " FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid"
                " WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 4.0, 3.0, 1.0) LIMIT ?",
                (match, max(1, int(limit))),
            ).fetchall()
        keys = ("id", "path", "symbol", "kind", "start_line", "end_line", "content", "content_hash")
        return [dict(zip(keys, row)) for row in rows], total

    def count(self) -> int:
        with closing(self._connect()) as connection, connection:
            return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])
