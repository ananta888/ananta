from __future__ import annotations

import time
from typing import Any

from agent.common.audit import log_audit
from agent.db_models import ArchivedTaskDB, TaskDB
from agent.services.repository_registry import get_repository_registry
from agent.services.task_state_machine_service import can_transition, resolve_next_status
from agent.services.task_status_service import normalize_task_status
from agent.services.task_runtime_service import update_local_task_status


class TaskAdminService:
    """Hub-owned task administration use-cases for archive, restore, hierarchy, and interventions."""

    def parse_status_filters(self, raw: object) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(raw, list):
            parts = [str(p).strip() for p in raw if str(p).strip()]
        else:
            parts = []
        return {normalize_task_status(p, default="") for p in parts if normalize_task_status(p, default="")}

    def task_matches_filters(
        self,
        task: dict,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
    ) -> bool:
        if statuses and normalize_task_status(task.get("status"), default="") not in statuses:
            return False
        if team_id and (task.get("team_id") or "") != team_id:
            return False
        if before_ts is not None and float(task.get("updated_at") or task.get("created_at") or 0.0) >= before_ts:
            return False
        if task_ids and (task.get("id") or "") not in task_ids:
            return False
        return True

    def load_all_archived_tasks(self) -> list[dict]:
        repos = get_repository_registry()
        items: list[dict] = []
        limit = 500
        offset = 0
        while True:
            chunk = repos.archived_task_repo.get_all(limit=limit, offset=offset)
            if not chunk:
                break
            items.extend([item.model_dump() for item in chunk])
            if len(chunk) < limit:
                break
            offset += limit
        return items

    def build_task_tree(self, *, root_id: str, include_archived: bool, max_depth: int) -> dict | None:
        repos = get_repository_registry()
        active_items = [t.model_dump() for t in repos.task_repo.get_all()]
        archived_items = self.load_all_archived_tasks() if include_archived else []
        by_id: dict[str, dict] = {}
        children_by_parent: dict[str, list[str]] = {}

        for item in archived_items:
            item["_source"] = "archived"
            by_id[item["id"]] = item
        for item in active_items:
            item["_source"] = "active"
            by_id[item["id"]] = item

        for tid, item in by_id.items():
            parent_id = str(item.get("parent_task_id") or "").strip()
            if parent_id:
                children_by_parent.setdefault(parent_id, []).append(tid)

        if root_id not in by_id:
            return None

        def _node(task_id: str, depth: int, lineage: set[str]) -> dict:
            task = dict(by_id[task_id])
            child_ids = children_by_parent.get(task_id, [])
            out = {"task": task, "depth": depth, "children": [], "children_count": len(child_ids)}
            if depth >= max_depth:
                out["truncated"] = True
                return out
            for child_id in child_ids:
                if child_id in lineage:
                    out["children"].append({"task_id": child_id, "cycle_detected": True})
                    continue
                out["children"].append(_node(child_id, depth + 1, lineage | {child_id}))
            return out

        return _node(root_id, 0, {root_id})

    def archive_task(self, *, task_id: str) -> bool:
        repos = get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if not task:
            return False
        repos.archived_task_repo.save(ArchivedTaskDB(**task.model_dump()))
        repos.task_repo.delete(task_id)
        return True

    def archive_tasks(
        self,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
    ) -> list[str]:
        repos = get_repository_registry()
        archived_ids: list[str] = []
        for task in repos.task_repo.get_all():
            item = task.model_dump()
            if not self.task_matches_filters(item, statuses=statuses, team_id=team_id, before_ts=before_ts, task_ids=task_ids):
                continue
            repos.archived_task_repo.save(ArchivedTaskDB(**item))
            repos.task_repo.delete(item["id"])
            archived_ids.append(item["id"])
        return archived_ids

    def restore_task(self, *, task_id: str) -> bool:
        repos = get_repository_registry()
        archived = repos.archived_task_repo.get_by_id(task_id)
        if not archived:
            return False
        task = TaskDB(**archived.model_dump())
        if task.status == "archived":
            task.status = "todo"
        repos.task_repo.save(task)
        repos.archived_task_repo.delete(task_id)
        return True

    def restore_tasks(
        self,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
    ) -> list[str]:
        restored_ids: list[str] = []
        for archived in self.load_all_archived_tasks():
            if not self.task_matches_filters(archived, statuses=statuses, team_id=team_id, before_ts=before_ts, task_ids=task_ids):
                continue
            task = TaskDB(**archived)
            if task.status == "archived":
                task.status = "todo"
            repos = get_repository_registry()
            repos.task_repo.save(task)
            repos.archived_task_repo.delete(task.id)
            restored_ids.append(task.id)
        return restored_ids

    def cleanup_archived_tasks(
        self,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
    ) -> tuple[list[str], list[dict]]:
        deleted_ids: list[str] = []
        errors: list[dict] = []
        for item in self.load_all_archived_tasks():
            if not self.task_matches_filters(item, statuses=statuses, team_id=team_id, before_ts=before_ts, task_ids=task_ids):
                continue
            tid = item.get("id")
            try:
                get_repository_registry().archived_task_repo.delete(tid)
                deleted_ids.append(tid)
            except Exception as exc:
                errors.append({"id": tid, "error": str(exc)})
        return deleted_ids, errors

    def apply_archive_retention(self, *, team_id: str, statuses: set[str], cutoff: float) -> list[str]:
        deleted_ids: list[str] = []
        for item in self.load_all_archived_tasks():
            archived_at = float(item.get("archived_at") or item.get("updated_at") or 0)
            if archived_at >= cutoff:
                continue
            if team_id and (item.get("team_id") or "") != team_id:
                continue
            if statuses and normalize_task_status(item.get("status"), default="") not in statuses:
                continue
            get_repository_registry().archived_task_repo.delete(item["id"])
            deleted_ids.append(item["id"])
        return deleted_ids

    def cleanup_active_tasks(
        self,
        *,
        mode: str,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
    ) -> tuple[list[dict], list[str], list[str], list[dict]]:
        repos = get_repository_registry()
        matched = [
            item
            for task in repos.task_repo.get_all()
            for item in [task.model_dump()]
            if self.task_matches_filters(item, statuses=statuses, team_id=team_id, before_ts=before_ts, task_ids=task_ids)
        ]
        archived_ids: list[str] = []
        deleted_ids: list[str] = []
        errors: list[dict] = []
        for item in matched:
            tid = item.get("id")
            try:
                if mode == "archive":
                    repos.archived_task_repo.save(ArchivedTaskDB(**item))
                    repos.task_repo.delete(tid)
                    archived_ids.append(tid)
                else:
                    repos.task_repo.delete(tid)
                    deleted_ids.append(tid)
            except Exception as exc:
                errors.append({"id": tid, "error": str(exc)})
        return matched, archived_ids, deleted_ids, errors

    def intervene_task(self, *, task_id: str, action: str, actor: str) -> tuple[bool, str, dict]:
        task = get_repository_registry().task_repo.get_by_id(task_id)
        if not task:
            return False, "not_found", {}

        current = normalize_task_status(task.status, default="")
        ok, reason = can_transition(action, current)
        if not ok:
            return False, reason, {"current_status": current}
        new_status = resolve_next_status(action, current, assigned_agent_url=task.assigned_agent_url)

        update_kwargs: dict = {}
        if action == "retry":
            update_kwargs["last_exit_code"] = None
        update_local_task_status(
            task_id,
            new_status,
            event_type="task_intervention",
            event_actor=actor,
            event_details={"action": action, "previous_status": current, "new_status": new_status},
            manual_override_until=time.time() + 600,
            **update_kwargs,
        )
        log_audit(
            "task_intervention",
            {"task_id": task_id, "action": action, "actor": actor, "previous_status": current, "new_status": new_status},
        )
        return True, "ok", {"id": task_id, "action": action, "status": new_status}


task_admin_service = TaskAdminService()


def get_task_admin_service() -> TaskAdminService:
    return task_admin_service
