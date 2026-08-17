"""Optional DuckDB FTS adapter. Production default remains SQLite FTS5."""

from __future__ import annotations

from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorStoreError


class DuckDBFtsStore:
    def __init__(self, config: DuckDBVectorStoreConfig) -> None:
        self._config = config

    def search(self, query: str, *, limit: int = 8) -> list[dict]:
        if not self._config.fts_enabled:
            raise VectorStoreError("duckdb_fts_disabled")
        raise VectorStoreError("duckdb_fts_not_parity_gated")
