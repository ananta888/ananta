from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.routes.tasks.timeline_utils import is_error_timeline_event, task_timeline_events
from agent.services.repository_registry import get_repository_registry
from agent.services.task_read_access_service import TaskReadAccessContext
from agent.services.task_read_projection_service import (
    get_task_read_projection_service,
)
from agent.services.task_status_service import expand_task_status_query_values, normalize_task_status
from agent.services.worker_workspace_service import get_worker_workspace_service

_TASK_READ_PAGE_SIZE_MAX = 1_000
_TASK_READ_SCAN_CHUNK_SIZE = 250
_TASK_READ_SCAN_MAX_ROWS = 10_000


def _repository_scope_filters(
    access: TaskReadAccessContext,
) -> dict[str, str]:
    if access.principal.is_admin:
        return {}
    return {
        "tenant_id": str(access.principal.tenant_id or ""),
        "project_id": str(access.principal.project_id or ""),
    }


def _access_filtered_page(
    *,
    fetch_page: Callable[[int, int], list[Any]],
    access: TaskReadAccessContext,
    limit: int,
    offset: int,
) -> list[dict]:
    """Apply offset/limit to authorized rows, within explicit scan bounds."""

    page_limit = min(max(int(limit), 0), _TASK_READ_PAGE_SIZE_MAX)
    authorized_offset = max(int(offset), 0)
    if page_limit == 0:
        return []

    authorized_seen = 0
    scan_offset = 0
    scanned_rows = 0
    page: list[dict] = []
    while (
        scanned_rows < _TASK_READ_SCAN_MAX_ROWS
        and len(page) < page_limit
    ):
        chunk_limit = min(
            _TASK_READ_SCAN_CHUNK_SIZE,
            _TASK_READ_SCAN_MAX_ROWS - scanned_rows,
        )
        rows = list(fetch_page(chunk_limit, scan_offset) or [])[
            :chunk_limit
        ]
        if not rows:
            break
        scanned_rows += len(rows)
        scan_offset += len(rows)
        for row in rows:
            payload = row.model_dump()
            if not access.can_read(payload):
                continue
            if authorized_seen < authorized_offset:
                authorized_seen += 1
                continue
            page.append(payload)
            authorized_seen += 1
            if len(page) >= page_limit:
                break
        if len(rows) < chunk_limit:
            break
    return page


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
        access: TaskReadAccessContext | None = None,
    ) -> list[dict]:
        repos = get_repository_registry()
        status_values = expand_task_status_query_values(normalize_task_status(status_filter, default=""))
        projection = get_task_read_projection_service()
        query = {
            "status": None,
            "status_values": status_values or None,
            "agent": agent_filter,
            "since": since_filter,
            "until": until_filter,
        }
        if access is not None:
            query.update(_repository_scope_filters(access))
        if access is None:
            payloads = [
                task.model_dump()
                for task in repos.task_repo.get_paged(
                    limit=limit,
                    offset=offset,
                    **query,
                )
            ]
        else:
            payloads = _access_filtered_page(
                fetch_page=lambda chunk_limit, chunk_offset: (
                    repos.task_repo.get_paged(
                        limit=chunk_limit,
                        offset=chunk_offset,
                        **query,
                    )
                ),
                access=access,
                limit=limit,
                offset=offset,
            )
        return [projection.summary(task) for task in payloads]

    def timeline(
        self,
        *,
        team_id_filter: str | None,
        agent_filter: str | None,
        status_filter: str | None,
        error_only: bool,
        since_filter: float | None,
        limit: int,
        access: TaskReadAccessContext | None = None,
    ) -> dict:
        repos = get_repository_registry()
        projection = get_task_read_projection_service()
        events: list[dict] = []
        normalized_status = normalize_task_status(status_filter, default="") if status_filter else ""
        for task_obj in repos.task_repo.get_all():
            task = task_obj.model_dump()
            if access is not None and not access.can_read(task):
                continue
            if team_id_filter and (task.get("team_id") or "") != team_id_filter:
                continue
            if normalized_status and normalize_task_status(task.get("status"), default="") != normalized_status:
                continue
            for event in task_timeline_events(task):
                event = projection.history_event(event)
                if event is None:
                    continue
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

    def list_archived_tasks(
        self,
        *,
        limit: int,
        offset: int,
        access: TaskReadAccessContext | None = None,
    ) -> list[dict]:
        repos = get_repository_registry()
        if access is None:
            payloads = [
                task.model_dump()
                for task in repos.archived_task_repo.get_all(
                    limit=limit,
                    offset=offset,
                )
            ]
        else:
            scope_filters = _repository_scope_filters(access)
            payloads = _access_filtered_page(
                fetch_page=lambda chunk_limit, chunk_offset: (
                    repos.archived_task_repo.get_all(
                        limit=chunk_limit,
                        offset=chunk_offset,
                        **scope_filters,
                    )
                ),
                access=access,
                limit=limit,
                offset=offset,
            )
        projection = get_task_read_projection_service()
        return [projection.summary(task) for task in payloads]

    def task_tree(
        self,
        *,
        root_id: str,
        include_archived: bool,
        max_depth: int,
        task_admin_service,
        access: TaskReadAccessContext | None = None,
    ) -> dict | None:
        tree = task_admin_service.build_task_tree(
            root_id=root_id,
            include_archived=include_archived,
            max_depth=max_depth,
        )
        if tree is None:
            return None
        projection = get_task_read_projection_service()
        return projection.tree(
            tree,
            can_read=access.can_read if access is not None else lambda _task: True,
        )

    def task_hierarchy_view(
        self,
        *,
        root_id: str,
        include_archived: bool,
        max_depth: int,
        task_admin_service,
        access: TaskReadAccessContext | None = None,
    ) -> dict | None:
        tree = self.task_tree(
            root_id=root_id,
            include_archived=include_archived,
            max_depth=max_depth,
            task_admin_service=task_admin_service,
            access=access,
        )
        if not tree:
            return None
        return {
            "root_task_id": root_id,
            "tree": tree,
            "ui_actions": ["assign", "unassign", "pause", "resume", "cancel", "retry", "archive"],
        }

    def delete_archived_task(self, *, task_id: str) -> dict | None:
        # Compatibility adapter: writes belong to TaskAdminService, which
        # applies the Recovery-lineage fence before a permanent purge.
        from agent.services.task_admin_service import (
            get_task_admin_service,
        )

        if not get_task_admin_service().delete_archived_task(
            task_id=task_id
        ):
            return None
        return {"deleted_count": 1, "deleted_ids": [task_id]}

    def task_workspace_files(
        self,
        *,
        task_id: str,
        tracked_only: bool,
        max_entries: int,
    ) -> dict | None:
        repos = get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if not task:
            return None

        task_payload = task.model_dump()
        execution_context = dict(task_payload.get("worker_execution_context") or {})
        workspace_meta = dict(execution_context.get("workspace") or {})
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task_payload)
        listing = get_worker_workspace_service().list_workspace_files(
            workspace_dir=workspace_context.workspace_dir,
            tracked_only=tracked_only,
            max_entries=max_entries,
        )
        return {
            "task_id": task_payload.get("id"),
            "workspace": {
                "scope_key": workspace_meta.get("scope_key"),
                "worker_job_id": workspace_meta.get("worker_job_id") or task_payload.get("current_worker_job_id"),
                "agent_url": workspace_meta.get("agent_url") or task_payload.get("assigned_agent_url"),
                "agent_name": workspace_meta.get("agent_name"),
                **listing,
            },
        }


task_query_service = TaskQueryService()


def get_task_query_service() -> TaskQueryService:
    return task_query_service
