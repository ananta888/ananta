"""Bounded read-only search adapter for CodeCompass teaching index records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable


class ClassroomMaterialSearchService:
    def __init__(self, config_provider: Callable[[], dict]) -> None:
        self.config_provider = config_provider

    def search(self, query: str, filters: dict) -> list[dict]:
        classroom = (self.config_provider() or {}).get("classroom") or {}
        index_file = str(classroom.get("teaching_index_file") or "").strip()
        if not index_file:
            return []
        path = Path(index_file)
        if not path.is_file():
            return []
        terms = {term for term in re.findall(r"[\w-]{3,}", str(query).lower())}
        matches: list[dict] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            if record.get("kind") not in {"teaching_task", "teaching_hint", "known_solution"}:
                continue
            module_scope = filters.get("module_scope")
            task_scope = filters.get("task_scope")
            if module_scope and str(record.get("module_id") or "") != str(module_scope):
                continue
            if task_scope and str(record.get("task_id") or "") != str(task_scope):
                continue
            haystack = " ".join(str(record.get(key) or "") for key in ("name", "summary", "embedding_text")).lower()
            overlap = sum(1 for term in terms if term in haystack)
            if not overlap:
                continue
            matches.append(
                {
                    "module_id": record.get("module_id"),
                    "task_id": record.get("task_id"),
                    "title": record.get("name"),
                    "score": min(1.0, 0.45 + overlap / max(2, len(terms))),
                    "file": record.get("file"),
                    "excerpt": str(record.get("summary") or record.get("embedding_text") or "")[:500],
                }
            )
        return sorted(matches, key=lambda item: -float(item["score"]))[:10]
