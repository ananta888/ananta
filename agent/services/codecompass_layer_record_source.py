"""Search candidates for knowledge indices that point at a CodeCompass layer head.

A pointer row (``index_metadata[POINTER_KEY]``) stands for the effective
``chunks`` view of one layer profile. Before each query the profile's FTS
projection is brought to the current head (cheap when nothing changed,
incremental when deltas were appended), then it preselects candidates in the
record shape the existing scorer and passage logic expect.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.codecompass_layer_search_index import LayerSearchIndex

POINTER_KEY = "codecompass_layer_head"
POINTER_SCHEMA = "ananta.codecompass_layer_pointer.v1"
_INDICES: dict[tuple[str, str], LayerSearchIndex] = {}
_INDICES_LOCK = threading.Lock()


def pointer_profile(knowledge_index: Any) -> str | None:
    metadata = getattr(knowledge_index, "index_metadata", None)
    pointer = metadata.get(POINTER_KEY) if isinstance(metadata, Mapping) else None
    if not isinstance(pointer, Mapping) or pointer.get("schema") != POINTER_SCHEMA:
        return None
    return str(pointer.get("profile_id") or "") or None


def head_chain(head: Mapping[str, Any]) -> list[str]:
    chain = [str(value) for _key, value in sorted(dict(head.get("base_layer_set") or {}).items()) if value]
    for delta in list(head.get("ordered_delta_sets") or []):
        normalized = dict(delta) if isinstance(delta, Mapping) else {"default": str(delta)}
        chain.extend(str(value) for _key, value in sorted(normalized.items()) if value)
    return chain


def search_index(root: str | Path, profile_id: str) -> LayerSearchIndex:
    import hashlib

    key = (str(root), profile_id)
    with _INDICES_LOCK:
        if key not in _INDICES:
            name = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:32]
            _INDICES[key] = LayerSearchIndex(Path(root) / "search" / f"{name}.sqlite")
        return _INDICES[key]


class LayerRecordSource:
    def __init__(self, *, root: str | Path, profile_id: str, layers: Any, heads: Any) -> None:
        self._root = Path(root)
        self._profile = profile_id
        self._layers = layers
        self._heads = heads

    def sync(self) -> str:
        head = self._heads.get_head(self._profile)
        if not head:
            return "no_head"
        return search_index(self._root, self._profile).sync(
            generation=int(head.get("generation") or 0),
            chain=head_chain(head),
            load_layer=self._layers.get_layer,
        )

    def candidates(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        if self.sync() == "no_head":
            return [], 0
        head = self._heads.get_head(self._profile) or {}
        rows, total = search_index(self._root, self._profile).search(query, limit=limit)
        revision = str(head.get("effective_source_revision") or "")
        return [_record(row, revision) for row in rows], total


def _record(row: Mapping[str, Any], revision: str) -> dict[str, Any]:
    return {
        "id": row["id"],
        "path": row["path"],
        "file": row["path"],
        "symbol": row["symbol"],
        "kind": row["kind"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "content": row["content"],
        "content_hash": row["content_hash"],
        "revision": revision,
    }


def layer_record_source(knowledge_index: Any) -> LayerRecordSource | None:
    """The source for a pointer index, when this Hub has layers wired; else ``None``."""
    profile_id = pointer_profile(knowledge_index)
    if profile_id is None:
        return None
    try:
        from flask import current_app

        root = current_app.extensions.get("codecompass_layer_root")
    except RuntimeError:
        root = None
    if not root:
        return None
    from worker.incremental_index.head_registry import LayerHeadRegistry
    from worker.incremental_index.layer_store import ArtifactLayerStore

    return LayerRecordSource(root=root, profile_id=profile_id, layers=ArtifactLayerStore(root),
                             heads=LayerHeadRegistry(root))
