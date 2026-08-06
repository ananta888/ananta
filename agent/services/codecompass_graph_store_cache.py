"""Bounded cache for immutable, Hub-admitted CodeCompass read snapshots."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from threading import RLock

from ananta_codecompass.graph_store import CodeCompassGraphStore
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)


def _file_signature(path: Path | None) -> tuple[str, int, int, int]:
    if path is None:
        return ("", -1, -1, -1)
    try:
        stat = path.stat()
    except OSError:
        return (str(path), -1, -1, -1)
    return (
        str(path),
        int(stat.st_ino),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


class CodeCompassGraphStoreCache:
    """Reuse parsed graph payloads while file identity remains unchanged.

    Artifact authorization and digest verification stay in the resolver.  This
    cache only avoids reparsing an already resolved immutable snapshot.
    """

    def __init__(
        self,
        *,
        maximum_entries: int = 4,
        maximum_source_bytes: int = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES * 2,
    ) -> None:
        if maximum_entries < 1:
            raise ValueError("graph_store_cache_size_invalid")
        if maximum_source_bytes < 1:
            raise ValueError("graph_store_cache_bytes_invalid")
        self._maximum_entries = int(maximum_entries)
        self._maximum_source_bytes = int(maximum_source_bytes)
        self._source_bytes = 0
        self._stores: OrderedDict[
            tuple[tuple[str, int, int, int], tuple[str, int, int, int]],
            tuple[CodeCompassGraphStore, int],
        ] = OrderedDict()
        self._lock = RLock()

    def get(
        self,
        *,
        index_path: str | Path,
        visual_metrics_path: str | Path | None,
    ) -> CodeCompassGraphStore:
        graph_path = Path(index_path)
        metrics_path = Path(visual_metrics_path) if visual_metrics_path is not None else None
        key = (_file_signature(graph_path), _file_signature(metrics_path))
        source_bytes = sum(max(0, signature[3]) for signature in key)
        with self._lock:
            existing = self._stores.pop(key, None)
            if existing is not None:
                self._stores[key] = existing
                return existing[0]
            store = CodeCompassGraphStore(
                index_path=graph_path,
                max_artifact_bytes=MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
                visual_metrics_path=metrics_path,
            )
            if source_bytes > self._maximum_source_bytes:
                return store
            self._stores[key] = (store, source_bytes)
            self._source_bytes += source_bytes
            while len(self._stores) > self._maximum_entries or self._source_bytes > self._maximum_source_bytes:
                _evicted_key, (_evicted_store, evicted_bytes) = self._stores.popitem(last=False)
                self._source_bytes -= evicted_bytes
            return store

    def clear(self) -> None:
        with self._lock:
            self._stores.clear()
            self._source_bytes = 0


codecompass_graph_store_cache = CodeCompassGraphStoreCache()


def get_codecompass_graph_store_cache() -> CodeCompassGraphStoreCache:
    return codecompass_graph_store_cache


__all__ = [
    "CodeCompassGraphStoreCache",
    "get_codecompass_graph_store_cache",
]
