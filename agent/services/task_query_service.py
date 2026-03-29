from __future__ import annotations

from agent.services.repository_registry import get_repository_registry
from agent.services.task_status_service import expand_task_status_query_values, normalize_task_status
from agent.routes.tasks.timeline_utils import is_error_timeline_event, task_timeline_events


class TaskQueryService:
    """Read-model and query use-cases for task listing, timeline, archive views, and hierarchy views."""

    def list_tasks(
        self,
        *,
        status_filter: str,
        agent_filter: str | None,
        since_filter: float | None,
        until_filter: float | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        repos = get_repository_registry()
        status_values = expand_task_status_query_values(normalize_task_status(status_filter, default=""))
        tasks = repos.task_repo.get_paged(
            limit=limit,
            offset=offset,
            status=None,
            status_values=status_values or None,
            agent=agent_filter,
            since=since_filter,
            until=until_filter,
        )
        return [task.model_dump() for task in tasks]

    def timeline(
        self,
        *,
        team_id_filter: str | None,
        agent_filter: str | None,
        status_filter: str | None,
        error_only: bool,
        since_filter: float | None,
        limit: int,
    ) -> dict:
        repos = get_repository_registry()
        events: list[dict] = []
        normalized_status = normalize_task_status(status_filter, default="") if status_filter else ""
        for task_obj in repos.task_repo.get_all():
            task = task_obj.model_dump()
            if team_id_filter and (task.get("team_id") or "") != team_id_filter:
                continue
            if normalized_status and normalize_task_status(task.get("status"), default="") != normalized_status:
                continue
            for event in task_timeline_events(task):
                ts = event.get("timestamp") or 0
                if since_filter and ts < since_filter:
                    continue
                if agent_filter and event.get("actor") != agent_filter:
                    continue
                if error_only and not is_error_timeline_event(event):
                    continue
                events.append(event)
        events.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        return {"items": events[:limit], "total": len(events)}

    def list_archived_tasks(self, *, limit: int, offset: int) -> list[dict]:
        repos = get_repository_registry()
        return [task.model_dump() for task in repos.archived_task_repo.get_all(limit=limit, offset=offset)]

    def task_tree(self, *, root_id: str, include_archived: bool, max_depth: int, task_admin_service) -> dict | None:
        return task_admin_service.build_task_tree(root_id=root_id, include_archived=include_archived, max_depth=max_depth)

    def task_hierarchy_view(self, *, root_id: str, include_archived: bool, max_depth: int, task_admin_service) -> dict | None:
        tree = self.task_tree(
            root_id=root_id,
            include_archived=include_archived,
            max_depth=max_depth,
            task_admin_service=task_admin_service,
        )
        if not tree:
            return None
        return {
            "root_task_id": root_id,
            "tree": tree,
            "ui_actions": ["assign", "unassign", "pause", "resume", "cancel", "retry", "archive"],
        }

    def delete_archived_task(self, *, task_id: str) -> dict | None:
        repos = get_repository_registry()
        archived = repos.archived_task_repo.get_by_id(task_id)
        if not archived:
            return None
        repos.archived_task_repo.delete(task_id)
        return {"deleted_count": 1, "deleted_ids": [task_id]}


task_query_service = TaskQueryService()


def get_task_query_service() -> TaskQueryService:
    return task_query_service
