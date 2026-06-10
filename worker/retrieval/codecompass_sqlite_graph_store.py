"""Optional SQLite backend for the CodeCompass graph (CCAQE-021/022).

Persists nodes and edges in relational tables instead of one JSON file, but
keeps the exact CodeCompassGraphStore contract by reusing its index building,
lookup and traversal logic — only load/save are storage specific. Uses only
sqlite3 from the standard library and is NOT the default store; callers opt in
explicitly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore

_EDGE_ATTRIBUTE_KEYS = ("field", "operation", "heuristic")


class CodeCompassSqliteGraphStore(CodeCompassGraphStore):
    def __init__(self, *, db_path: str | Path):
        self._db_path = Path(db_path)
        self._cached_payload: dict[str, Any] | None = None

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._db_path))

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cc_graph_state (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cc_graph_nodes (
              node_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              name TEXT,
              file TEXT,
              record_id TEXT,
              content TEXT,
              source_record TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_nodes_name ON cc_graph_nodes(name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_nodes_file ON cc_graph_nodes(file);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_nodes_record ON cc_graph_nodes(record_id);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cc_graph_edges (
              source_id TEXT NOT NULL,
              target_id TEXT NOT NULL,
              edge_type TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 1.0,
              field TEXT,
              operation TEXT,
              heuristic TEXT,
              provenance TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_edges_source ON cc_graph_edges(source_id, edge_type);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_edges_target ON cc_graph_edges(target_id, edge_type);")

    def load(self) -> dict[str, Any]:
        if self._cached_payload is not None:
            return self._cached_payload
        if not self._db_path.exists():
            self._cached_payload = {
                "state": {},
                "nodes": [],
                "edges": [],
                "node_index": {},
                "outgoing_index": {},
                "incoming_index": {},
                "diagnostics": {"status": "degraded", "reason": "graph_index_missing"},
            }
            return self._cached_payload
        with self._connect() as conn:
            self._ensure_schema(conn)
            state_rows = conn.execute("SELECT key, value FROM cc_graph_state").fetchall()
            node_rows = conn.execute(
                "SELECT node_id, kind, name, file, record_id, content, source_record FROM cc_graph_nodes ORDER BY node_id"
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT source_id, target_id, edge_type, confidence, field, operation, heuristic, provenance"
                " FROM cc_graph_edges ORDER BY source_id, target_id, edge_type"
            ).fetchall()

        state: dict[str, Any] = {}
        diagnostics: dict[str, Any] = {}
        for key, value in state_rows:
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                parsed = value
            if key == "diagnostics":
                diagnostics = dict(parsed) if isinstance(parsed, dict) else {}
            else:
                state[key] = parsed

        nodes: list[dict[str, Any]] = []
        for row in node_rows:
            source_record: dict[str, Any] = {}
            if row[6]:
                try:
                    source_record = json.loads(row[6])
                except (TypeError, ValueError):
                    source_record = {}
            nodes.append({
                "id": str(row[0]),
                "kind": str(row[1] or "unknown"),
                "name": str(row[2] or ""),
                "file": str(row[3] or ""),
                "record_id": str(row[4] or ""),
                "content": str(row[5] or ""),
                "source_record": source_record,
            })

        edges: list[dict[str, Any]] = []
        for row in edge_rows:
            edge: dict[str, Any] = {
                "source_id": str(row[0]),
                "target_id": str(row[1]),
                "edge_type": str(row[2]),
                "confidence": float(row[3] if row[3] is not None else 1.0),
            }
            for index, key in enumerate(_EDGE_ATTRIBUTE_KEYS, start=4):
                if row[index] is not None:
                    edge[key] = row[index]
            if row[7]:
                try:
                    edge["provenance"] = json.loads(row[7])
                except (TypeError, ValueError):
                    edge["provenance"] = {}
            edges.append(edge)

        node_index = self._build_node_index(nodes)
        outgoing_index, incoming_index = self._build_edge_indexes(edges)
        self._cached_payload = {
            "state": state,
            "nodes": nodes,
            "edges": edges,
            "node_index": node_index,
            "outgoing_index": outgoing_index,
            "incoming_index": incoming_index,
            "diagnostics": diagnostics or {"status": "ready", "reason": "graph_loaded", "node_count": len(nodes), "edge_count": len(edges)},
        }
        return self._cached_payload

    def save(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("DELETE FROM cc_graph_state;")
            conn.execute("DELETE FROM cc_graph_nodes;")
            conn.execute("DELETE FROM cc_graph_edges;")
            state = dict(payload.get("state") or {})
            for key, value in state.items():
                conn.execute(
                    "INSERT INTO cc_graph_state (key, value) VALUES (?, ?)",
                    (str(key), json.dumps(value, ensure_ascii=False)),
                )
            diagnostics = dict(payload.get("diagnostics") or {})
            conn.execute(
                "INSERT OR REPLACE INTO cc_graph_state (key, value) VALUES ('diagnostics', ?)",
                (json.dumps(diagnostics, ensure_ascii=False),),
            )
            for node in list(payload.get("nodes") or []):
                conn.execute(
                    "INSERT OR REPLACE INTO cc_graph_nodes (node_id, kind, name, file, record_id, content, source_record)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(node.get("id") or ""),
                        str(node.get("kind") or "unknown"),
                        str(node.get("name") or ""),
                        str(node.get("file") or ""),
                        str(node.get("record_id") or ""),
                        str(node.get("content") or ""),
                        json.dumps(dict(node.get("source_record") or {}), ensure_ascii=False),
                    ),
                )
            for edge in list(payload.get("edges") or []):
                conn.execute(
                    "INSERT INTO cc_graph_edges (source_id, target_id, edge_type, confidence, field, operation, heuristic, provenance)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(edge.get("source_id") or ""),
                        str(edge.get("target_id") or ""),
                        str(edge.get("edge_type") or "related"),
                        float(edge.get("confidence") or 1.0),
                        edge.get("field"),
                        edge.get("operation"),
                        edge.get("heuristic"),
                        json.dumps(dict(edge.get("provenance") or {}), ensure_ascii=False),
                    ),
                )
            conn.commit()
        self._cached_payload = None
