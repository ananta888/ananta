"""Thread-local DuckDB connections. Never share a connection across threads."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from worker.retrieval.duckdb_extension_policy import apply_runtime_settings
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorStoreError

try:
    import duckdb
except ImportError:  # pragma: no cover - optional extra
    duckdb = None


class DuckDBNotInstalledError(VectorStoreError):
    def __init__(self) -> None:
        super().__init__("duckdb_backend_not_installed")


class DuckDBConnectionFactory:
    def __init__(self, config: DuckDBVectorStoreConfig) -> None:
        self._config = config
        self._local = threading.local()

    @staticmethod
    def available() -> bool:
        return duckdb is not None

    def connect(self, path: str | Path, *, read_only: bool) -> Any:
        if duckdb is None:
            raise DuckDBNotInstalledError()
        target = Path(path)
        if read_only and not target.exists():
            raise VectorStoreError("duckdb_snapshot_missing")
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved = str(target.resolve())
        cache = getattr(self._local, "connections", None)
        if cache is None:
            cache = {}
            self._local.connections = cache
        for key in list(cache):
            cached_path, cached_ro = key
            if cached_path == resolved and cached_ro != bool(read_only):
                try:
                    cache[key].close()
                except Exception:
                    pass
                del cache[key]
        key = (resolved, bool(read_only))
        connection = cache.get(key)
        if connection is not None:
            return connection
        connection = duckdb.connect(str(target), read_only=read_only)
        apply_runtime_settings(connection, self._config)
        cache[key] = connection
        return connection

    def close_thread(self) -> None:
        cache = getattr(self._local, "connections", {})
        for connection in list(cache.values()):
            try:
                connection.close()
            except Exception:
                continue
        self._local.connections = {}
