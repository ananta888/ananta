"""Hub-side analytics facade. Agents only get named templates."""

from __future__ import annotations

from typing import Any, Mapping

from worker.retrieval.duckdb_analytics_store import DuckDBAnalyticsStore
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorScope, VectorStoreError


class CodeCompassDuckDBAnalyticsService:
    def __init__(self, config: DuckDBVectorStoreConfig | None = None, store: DuckDBAnalyticsStore | None = None) -> None:
        self._config = config or DuckDBVectorStoreConfig()
        self._store = store or DuckDBAnalyticsStore(self._config)

    def query(
        self,
        name: str,
        *,
        capability: Mapping[str, Any] | None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not capability:
            raise VectorStoreError("empty_scope")
        try:
            scope = VectorScope(
                workspace_id=str(capability.get("workspace_id") or ""),
                repository_id=str(capability.get("repository_id") or capability.get("workspace_id") or "default"),
                profile_name=str(capability.get("profile_name") or "default"),
                domain=str(capability.get("domain") or "codecompass"),
            )
        except ValueError as exc:
            raise VectorStoreError("empty_scope") from exc
        rows = self._store.query(name, scope=scope, params=params)
        return {"template": name, "rows": rows, "count": len(rows)}


_analytics = CodeCompassDuckDBAnalyticsService()


def get_codecompass_duckdb_analytics_service() -> CodeCompassDuckDBAnalyticsService:
    return _analytics
