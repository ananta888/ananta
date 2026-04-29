from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.retrieval.index_contract import validate_index_entries


class RetrievalIndexStore:
    def __init__(self, *, store_path: str | Path):
        self._store_path = Path(store_path)

    @property
    def store_path(self) -> Path:
        return self._store_path

    def load(self) -> dict[str, Any]:
        if not self._store_path.exists():
            return {"state": {}, "entries": []}
        payload = json.loads(self._store_path.read_text(encoding="utf-8"))
        entries = [item for item in list(payload.get("entries") or []) if isinstance(item, dict)]
        validate_index_entries(entries)
        return {"state": dict(payload.get("state") or {}), "entries": entries}

    def save(self, *, state: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        validate_index_entries(entries)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"state": dict(state or {}), "entries": list(entries or [])}
        self._store_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def upsert_entries(self, *, state: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        current = self.load()
        by_id: dict[str, dict[str, Any]] = {}
        for item in list(current.get("entries") or []):
            by_id[str(item.get("chunk_id") or "")] = dict(item)
        for item in list(entries or []):
            by_id[str(item.get("chunk_id") or "")] = dict(item)
        self.save(state=dict(state or {}), entries=[by_id[key] for key in sorted(by_id)])

    def remove_paths(self, *, state: dict[str, Any], paths: list[str]) -> None:
        current = self.load()
        denied = {str(item).strip() for item in list(paths or []) if str(item).strip()}
        remaining = [item for item in list(current.get("entries") or []) if str(item.get("path") or "") not in denied]
        self.save(state=dict(state or {}), entries=remaining)

