from __future__ import annotations

import hashlib
from pathlib import Path

from agent.config import settings


class SourceCache:
    def __init__(self, *, root: Path | None = None, max_bytes: int = 2 * 1024 * 1024 * 1024) -> None:
        base = Path(root or settings.data_dir).expanduser().resolve()
        self._root = base / "sources" / "cache"
        self._root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_bytes)

    def _source_dir(self, source_id: str) -> Path:
        target = self._root / str(source_id or "").strip()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _subdir(self, *, source_id: str, kind: str) -> Path:
        target = self._source_dir(source_id) / kind
        target.mkdir(parents=True, exist_ok=True)
        return target

    def put_raw(self, *, source_id: str, payload: str) -> Path:
        digest = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
        path = self._subdir(source_id=source_id, kind="raw") / f"{digest}.txt"
        if not path.exists():
            path.write_text(str(payload), encoding="utf-8")
        return path

    def put_extracted(self, *, source_id: str, payload: str) -> Path:
        digest = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
        path = self._subdir(source_id=source_id, kind="extracted") / f"{digest}.txt"
        if not path.exists():
            path.write_text(str(payload), encoding="utf-8")
        return path

    def clear_source(self, *, source_id: str) -> int:
        target = self._source_dir(source_id)
        removed = 0
        for path in target.rglob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def total_size_bytes(self) -> int:
        total = 0
        for path in self._root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

