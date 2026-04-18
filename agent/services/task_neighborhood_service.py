from __future__ import annotations

import re
from typing import Any

from agent.repository import memory_entry_repo as default_memory_entry_repo
from agent.repository import task_repo as default_task_repo


class TaskNeighborhoodService:
    """Derives task locality without creating worker-owned orchestration paths."""

    def __init__(self, *, task_repository=None, memory_entry_repository=None) -> None:
        self._task_repository = task_repository or default_task_repo
        self._memory_entry_repository = memory_entry_repository or default_memory_entry_repo

    def _dump(self, item: Any) -> dict[str, Any]:
        if item is None:
            return {}
        if isinstance(item, dict):
            return dict(item)
        if hasattr(item, "model_dump"):
            return item.model_dump()
        return dict(getattr(item, "__dict__", {}) or {})

    def _changed_files_for_task(self, task_id: str) -> list[str]:
        files: list[str] = []
        for entry in self._memory_entry_repository.get_by_task(task_id):
            metadata = dict(getattr(entry, "memory_metadata", {}) or {})
            structured = dict(metadata.get("structured_summary") or {})
            for item in structured.get("changed_files") or []:
                value = str(item or "").strip()
                if value and value not in files:
                    files.append(value)
        return files

    def _tokens_for_files(self, files: list[str]) -> set[str]:
        tokens: set[str] = set()
        for file_path in files:
            for token in re.findall(r"[A-Za-z0-9_]+", str(file_path or "").lower()):
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    def build_neighborhood(self, task_id: str, *, limit: int = 8) -> dict[str, Any]:
        task = self._task_repository.get_by_id(task_id)
        if task is None:
            return {"task_id": task_id, "neighbor_task_ids": [], "neighbors": [], "reason": "task_not_found"}
        base = self._dump(task)
        base_files = self._changed_files_for_task(task_id)
        base_file_tokens = self._tokens_for_files(base_files)
        candidates: list[dict[str, Any]] = []
        for other_task in self._task_repository.get_all():
            other = self._dump(other_task)
            other_id = str(other.get("id") or "").strip()
            if not other_id or other_id == task_id:
                continue
            reasons: list[str] = []
            score = 0.0
            if base.get("goal_id") and base.get("goal_id") == other.get("goal_id"):
                reasons.append("same_goal")
                score += 0.5
            if base.get("plan_id") and base.get("plan_id") == other.get("plan_id"):
                reasons.append("same_plan")
                score += 0.45
            if other_id in set(base.get("depends_on") or []):
                reasons.append("declared_dependency")
                score += 0.6
            if other.get("parent_task_id") == task_id or base.get("parent_task_id") == other_id:
                reasons.append("parent_child")
                score += 0.55
            other_files = self._changed_files_for_task(other_id)
            overlap = sorted(base_file_tokens.intersection(self._tokens_for_files(other_files)))
            if overlap:
                reasons.append("file_symbol_overlap")
                score += min(0.7, 0.2 + len(overlap) * 0.08)
            if not reasons:
                continue
            candidates.append(
                {
                    "task_id": other_id,
                    "score": round(score, 4),
                    "reasons": reasons,
                    "changed_files": other_files[:8],
                    "overlap_terms": overlap[:12],
                }
            )
        candidates.sort(key=lambda item: (-float(item["score"]), str(item["task_id"])))
        selected = candidates[: max(1, int(limit))]
        return {
            "task_id": task_id,
            "neighbor_task_ids": [item["task_id"] for item in selected],
            "neighbors": selected,
            "changed_files": base_files[:8],
            "reason": "ok",
        }


task_neighborhood_service = TaskNeighborhoodService()


def get_task_neighborhood_service() -> TaskNeighborhoodService:
    return task_neighborhood_service
