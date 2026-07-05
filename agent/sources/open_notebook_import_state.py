from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent.config import settings


class OpenNotebookImportStateStore:
    """Persists non-content import identities used for idempotent re-imports."""

    def __init__(self, *, root: Path | None = None) -> None:
        base = Path(root or settings.data_dir).expanduser().resolve()
        self._root = base / "sources" / "open-notebook-import-state"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, registry_source_id: str) -> Path:
        normalized = str(registry_source_id or "").strip()
        if not normalized:
            raise ValueError("registry_source_id_required")
        return self._root / f"{normalized}.json"

    def load(self, registry_source_id: str) -> dict[str, Any]:
        path = self._path_for(registry_source_id)
        if not path.exists():
            return {"version": 1, "sources": {}, "notes": {}, "insights": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "sources": {}, "notes": {}, "insights": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "sources": {}, "notes": {}, "insights": {}}
        return {
            "version": 1,
            "sources": dict(payload.get("sources") or {}),
            "notes": dict(payload.get("notes") or {}),
            "insights": dict(payload.get("insights") or {}),
        }

    def save(self, registry_source_id: str, state: dict[str, Any]) -> dict[str, Any]:
        path = self._path_for(registry_source_id)
        payload = {
            "version": 1,
            "sources": dict(state.get("sources") or {}),
            "notes": dict(state.get("notes") or {}),
            "insights": dict(state.get("insights") or {}),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return payload
