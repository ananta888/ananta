from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PlannerCheckpointStore:
    def __init__(self, *, path: str | Path):
        self._path = Path(path)

    def save(self, *, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(dict(payload or {}), indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        return json.loads(self._path.read_text(encoding="utf-8"))

