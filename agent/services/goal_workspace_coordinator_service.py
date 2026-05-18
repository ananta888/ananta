from __future__ import annotations

import threading
import uuid
from typing import Any, Optional


class GoalWorkspaceCoordinatorService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._branches: dict[str, dict[str, dict]] = {}

    def register_branch(self, goal_id: str, branch: str, worker_url: Optional[str] = None) -> None:
        with self._lock:
            if goal_id not in self._branches:
                self._branches[goal_id] = {}
            if branch not in self._branches[goal_id]:
                self._branches[goal_id][branch] = {"ready": False, "worker_url": worker_url}

    def mark_branch_ready(self, goal_id: str, branch: str) -> None:
        with self._lock:
            if goal_id in self._branches and branch in self._branches[goal_id]:
                self._branches[goal_id][branch]["ready"] = True

    def get_merge_candidates(self, goal_id: str) -> list[str]:
        with self._lock:
            goal_branches = self._branches.get(goal_id, {})
            return [b for b, info in goal_branches.items() if info.get("ready")]

    def create_merge_task_after_goal_completion(
        self,
        *,
        goal_id: str,
        effective_config: dict,
        task_queue_service: Any,
        actor: str = "hub",
    ) -> Optional[dict]:
        git_workspace_cfg = dict((effective_config or {}).get("git_workspace") or {})
        if not git_workspace_cfg.get("enabled"):
            return None

        source_branches = self.get_merge_candidates(goal_id)
        if not source_branches:
            return None

        merge_strategy = str(git_workspace_cfg.get("merge_strategy") or "squash")
        target_branch = str(git_workspace_cfg.get("target_branch") or "main")

        task_id = f"merge-{uuid.uuid4()}"
        desc = f"git_merge: merge {len(source_branches)} branch(es) into {target_branch} via {merge_strategy}"

        task_queue_service.ingest_task(
            task_id=task_id,
            status="todo",
            title=desc[:200],
            description=desc,
            priority="high",
            created_by=actor,
            source="hub_merge_followup",
            goal_id=goal_id,
            event_type="task_ingested",
            event_channel="merge_followup",
            event_details={"goal_id": goal_id},
            extra_fields={
                "task_kind": "git_merge",
                "source_branches": source_branches,
                "target_branch": target_branch,
                "merge_strategy": merge_strategy,
                "derivation_reason": "goal_completion_merge",
            },
        )
        return {
            "id": task_id,
            "task_kind": "git_merge",
            "source_branches": source_branches,
            "target_branch": target_branch,
            "merge_strategy": merge_strategy,
        }


_instance: Optional[GoalWorkspaceCoordinatorService] = None
_instance_lock = threading.Lock()


def get_goal_workspace_coordinator_service() -> GoalWorkspaceCoordinatorService:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = GoalWorkspaceCoordinatorService()
        return _instance
