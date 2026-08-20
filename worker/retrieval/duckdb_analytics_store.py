"""Bounded analytics over a published DuckDB snapshot."""

from __future__ import annotations

from typing import Any, Mapping

from worker.retrieval.duckdb_query_templates import run_template
from worker.retrieval.duckdb_snapshot_manager import DuckDBSnapshotManager
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorScope


class DuckDBAnalyticsStore:
    def __init__(self, config: DuckDBVectorStoreConfig, snapshots: DuckDBSnapshotManager | None = None) -> None:
        self._config = config
        self._snapshots = snapshots or DuckDBSnapshotManager(config)

    def query(
        self,
        name: str,
        *,
        scope: VectorScope,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        connection = self._snapshots.open_active(scope, read_only=True)
        return run_template(
            connection,
            name=name,
            scope=scope,
            params=params,
            max_rows=self._config.resources.max_result_rows,
        )
