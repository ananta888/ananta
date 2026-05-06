from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.config import settings


class WikiImportCheckpointService:
    def __init__(self, *, root: Path | None = None) -> None:
        self._root = Path(root or (Path(settings.data_dir) / "wiki_checkpoints")).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _key(self, *, source_id: str, corpus_path: str, index_path: str | None = None) -> str:
        normalized = f"{source_id}|{Path(corpus_path).name}|{Path(index_path).name if index_path else ''}"
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def path_for(self, *, source_id: str, corpus_path: str, index_path: str | None = None) -> Path:
        return self._root / f"{self._key(source_id=source_id, corpus_path=corpus_path, index_path=index_path)}.json"

    def load(self, *, source_id: str, corpus_path: str, index_path: str | None = None) -> dict[str, Any] | None:
        path = self.path_for(source_id=source_id, corpus_path=corpus_path, index_path=index_path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def save(
        self,
        *,
        source_id: str,
        corpus_path: str,
        index_path: str | None = None,
        checkpoint: dict[str, Any],
    ) -> Path:
        path = self.path_for(source_id=source_id, corpus_path=corpus_path, index_path=index_path)
        path.write_text(json.dumps(dict(checkpoint or {}), ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

