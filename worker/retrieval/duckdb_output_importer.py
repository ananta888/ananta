"""Bounded import of CodeCompass records into a DuckDB snapshot."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorScope, VectorStoreError


class DuckDBOutputImporter:
    def __init__(self, config: DuckDBVectorStoreConfig) -> None:
        self._config = config

    def import_records(
        self,
        connection,
        *,
        records: Iterable[Mapping[str, Any]],
        scope: VectorScope,
        manifest_hash: str,
    ) -> dict[str, int]:
        imported = 0
        vectors = 0
        nodes = 0
        edges = 0
        budget = int(self._config.resources.max_import_bytes)
        used = 0
        for raw in records:
            if not isinstance(raw, Mapping):
                continue
            payload = dict(raw)
            used += len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
            if used > budget:
                raise VectorStoreError("duckdb_import_budget_exceeded")
            record_id = str(payload.get("id") or payload.get("record_id") or "").strip()
            path = str(payload.get("path") or payload.get("file") or "").strip()
            if not record_id or not path:
                continue
            connection.execute(
                """
                INSERT OR REPLACE INTO documents
                (record_id, workspace_id, repository_id, profile_name, domain, path, kind,
                 symbol, text, source_hash, manifest_hash, tombstone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    record_id,
                    scope.workspace_id,
                    scope.repository_id,
                    scope.profile_name,
                    scope.domain,
                    path,
                    str(payload.get("kind") or "record"),
                    str(payload.get("symbol") or ""),
                    str(payload.get("text") or payload.get("content") or payload.get("embedding_text") or "")[:8000],
                    str(payload.get("source_hash") or payload.get("content_hash") or record_id),
                    manifest_hash,
                    bool(payload.get("tombstone")),
                ],
            )
            imported += 1
            embedding = payload.get("embedding") or payload.get("vector")
            if isinstance(embedding, (list, tuple)) and embedding:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO vectors
                    (record_id, dimensions, embedding, model, distance, source_hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        record_id,
                        len(embedding),
                        [float(item) for item in embedding],
                        str(payload.get("model") or "local"),
                        "cosine",
                        str(payload.get("source_hash") or record_id),
                    ],
                )
                vectors += 1
            if payload.get("node_id") or payload.get("kind") in {"graph_node"}:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO graph_nodes
                    (node_id, kind, path, title, workspace_id, repository_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(payload.get("node_id") or record_id),
                        str(payload.get("kind") or "node"),
                        path,
                        str(payload.get("title") or payload.get("name") or record_id),
                        scope.workspace_id,
                        scope.repository_id,
                    ],
                )
                nodes += 1
            if payload.get("source") and payload.get("target"):
                edge_id = str(payload.get("edge_id") or f"{payload.get('source')}:{payload.get('target')}")
                connection.execute(
                    """
                    INSERT OR REPLACE INTO graph_edges
                    (edge_id, source_id, target_id, relation, origin)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        edge_id,
                        str(payload.get("source")),
                        str(payload.get("target")),
                        str(payload.get("relation") or payload.get("type") or "related"),
                        str(payload.get("origin") or "extracted"),
                    ],
                )
                edges += 1
        return {"documents": imported, "vectors": vectors, "nodes": nodes, "edges": edges}
