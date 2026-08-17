"""Parquet exchange for DuckDB snapshots. Not a second system of record."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.retrieval.duckdb_extension_policy import assert_allowed_extension, assert_safe_sql
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorStoreError


class DuckDBParquetExchange:
    def __init__(self, config: DuckDBVectorStoreConfig) -> None:
        self._config = config

    def export_documents(self, connection, destination: str | Path) -> dict[str, Any]:
        assert_allowed_extension("parquet", self._config.extensions.allowed)
        target = Path(destination)
        root = Path(self._config.snapshot_root).resolve()
        resolved = target.resolve()
        if root not in resolved.parents and resolved.parent != root:
            raise VectorStoreError("duckdb_parquet_path_outside_root")
        sql = "COPY (SELECT record_id, path, kind, symbol FROM documents WHERE tombstone = FALSE) TO ? (FORMAT PARQUET)"
        assert_safe_sql("SELECT record_id FROM documents")
        connection.execute(sql, [str(resolved)])
        return {"path": str(resolved), "format": "parquet"}
