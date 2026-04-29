from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from worker.retrieval.codecompass_query_parser import parse_codecompass_query


class CodeCompassFtsStore:
    def __init__(self, *, db_path: str | Path):
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._db_path))

    def diagnostics(self) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS cc_fts_probe USING fts5(content);")
                conn.execute("DROP TABLE IF EXISTS cc_fts_probe;")
            return {"status": "ready", "reason": "sqlite_fts5_available"}
        except sqlite3.DatabaseError:
            return {"status": "degraded", "reason": "sqlite_fts5_unavailable"}

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cc_metadata (
              row_id INTEGER PRIMARY KEY,
              record_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              file TEXT NOT NULL,
              parent_id TEXT,
              role_labels TEXT NOT NULL,
              importance_score REAL NOT NULL,
              generated_code INTEGER NOT NULL,
              source_manifest_hash TEXT NOT NULL,
              document_hash TEXT NOT NULL UNIQUE,
              retrieval_cache_state TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS cc_fts
            USING fts5(
              symbol_text,
              path_text,
              kind_text,
              summary_text,
              content_text,
              relation_text,
              focus_text,
              content='',
              tokenize='unicode61'
            );
            """
        )

    def rebuild(self, *, documents: list[dict[str, Any]], retrieval_cache_state: str) -> dict[str, Any]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("DELETE FROM cc_metadata;")
            conn.execute("DELETE FROM cc_fts;")
            for document in list(documents or []):
                text_fields = dict(document.get("text_fields") or {})
                cursor = conn.execute(
                    """
                    INSERT INTO cc_metadata (
                      record_id, kind, file, parent_id, role_labels, importance_score,
                      generated_code, source_manifest_hash, document_hash, retrieval_cache_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(document.get("record_id") or ""),
                        str(document.get("kind") or ""),
                        str(document.get("file") or ""),
                        str(document.get("parent_id") or ""),
                        ",".join(str(item) for item in list(document.get("role_labels") or [])),
                        float(document.get("importance_score") or 0.0),
                        1 if bool(document.get("generated_code")) else 0,
                        str(document.get("manifest_hash") or ""),
                        str(document.get("document_hash") or ""),
                        str(retrieval_cache_state or ""),
                    ),
                )
                row_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO cc_fts (rowid, symbol_text, path_text, kind_text, summary_text, content_text, relation_text, focus_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        str(text_fields.get("symbol_text") or ""),
                        str(text_fields.get("path_text") or ""),
                        str(text_fields.get("kind_text") or str(document.get("kind") or "")),
                        str(text_fields.get("summary_text") or ""),
                        str(text_fields.get("content_text") or ""),
                        str(text_fields.get("relation_text") or ""),
                        str(text_fields.get("focus_text") or ""),
                    ),
                )
            conn.commit()
        return {"status": "ok", "indexed_documents": len(list(documents or []))}

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        previous_retrieval_cache_state: str | None,
    ) -> dict[str, Any]:
        if str(previous_retrieval_cache_state or "") == str(retrieval_cache_state or ""):
            return {"status": "ok", "indexed_documents": 0, "mode": "unchanged"}
        result = self.rebuild(documents=documents, retrieval_cache_state=retrieval_cache_state)
        return {**result, "mode": "rebuild"}

    def search(self, *, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        parsed = parse_codecompass_query(query)
        match_query = " OR ".join(parsed["phrase_terms"] + parsed["exact_symbol_terms"] + parsed["broad_terms"]) or "''"
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                  m.record_id,
                  m.kind,
                  m.file,
                  m.parent_id,
                  m.role_labels,
                  m.importance_score,
                  m.generated_code,
                  m.source_manifest_hash,
                  m.document_hash,
                  bm25(cc_fts, 8.0, 4.0, 3.0, 2.0, 1.0, 2.0, 2.0) AS bm25_score,
                  cc_fts.symbol_text,
                  cc_fts.path_text
                FROM cc_fts
                JOIN cc_metadata m ON m.row_id = cc_fts.rowid
                WHERE cc_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (match_query, max(1, int(top_k) * 3)),
            ).fetchall()
        results: list[dict[str, Any]] = []
        exact_terms = {item.lower() for item in parsed["exact_symbol_terms"]}
        for rank_index, row in enumerate(rows):
            symbol_text = str(row[10] or "")
            path_text = str(row[11] or "")
            exact_haystack = " ".join([symbol_text, path_text, str(row[2] or ""), str(row[0] or "")]).lower()
            exact_hit = any(term in exact_haystack for term in exact_terms)
            boost = 3.0 if exact_hit else 1.0
            rank_score = 1.0 / float(rank_index + 1)
            bm25_raw = float(row[9] or 0.0)
            normalized_bm25 = max(0.0, -bm25_raw)
            score = (rank_score + normalized_bm25) * boost + float(row[5] or 0.0) * 0.01
            results.append(
                {
                    "record_id": str(row[0]),
                    "kind": str(row[1]),
                    "file": str(row[2]),
                    "parent_id": str(row[3] or ""),
                    "role_labels": [item for item in str(row[4] or "").split(",") if item],
                    "importance_score": float(row[5] or 0.0),
                    "generated_code": bool(row[6]),
                    "source_manifest_hash": str(row[7]),
                    "document_hash": str(row[8]),
                    "bm25_score": normalized_bm25,
                    "boost_breakdown": {
                        "exact_symbol_or_path_hit": exact_hit,
                        "exact_boost": boost,
                    },
                    "score": score,
                }
            )
        results.sort(key=lambda item: float(item["score"]), reverse=True)
        return results[: max(1, int(top_k))]
