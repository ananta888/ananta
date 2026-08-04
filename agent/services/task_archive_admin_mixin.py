"""Archive and retention use-cases shared by ``TaskAdminService``.

The mixin owns batch selection and error aggregation.  Atomic persistence and
lineage fencing remain narrow protected hooks on ``TaskAdminService``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.knowledge_index_task_ingress_policy import (
    KnowledgeIndexTaskMutationConflict,
)
from agent.services.task_status_service import normalize_task_status
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
)


@dataclass
class RecoveryChildAdminMutationConflict(RuntimeError):
    """Structured 409 conflict for an isolated Recovery-lineage mutation."""

    reason_code: str
    task_id: str
    source_task_id: str | None
    plan_id: str | None
    action: str

    def __post_init__(self) -> None:
        RuntimeError.__init__(
            self,
            f"{self.reason_code}:{self.task_id}",
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "task_id": self.task_id,
            "source_task_id": self.source_task_id,
            "plan_id": self.plan_id,
            "action": self.action,
            "http_status": 409,
        }


def _batch_error(task_id: Any, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, KnowledgeIndexTaskMutationConflict):
        return {
            "id": task_id,
            "error": exc.reason_code,
            **exc.as_data(),
        }
    if isinstance(exc, RecoveryChildAdminMutationConflict):
        return {
            "id": task_id,
            "error": exc.reason_code,
            **exc.as_data(),
        }
    if isinstance(exc, PermissionError):
        reason = str(exc)
        return {
            "id": task_id,
            "error": reason,
            "reason_code": reason,
            "http_status": 403,
        }
    return {"id": task_id, "error": str(exc)}


class TaskArchiveAdminMixin:
    """Filter and coordinate task archive/restore/cleanup operations."""

    def archive_task(
        self,
        *,
        task_id: str,
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> bool:
        removed, _snapshot = self._remove_active_task(
            task_id=task_id,
            archive=True,
            vector_authorization=vector_authorization,
        )
        return removed

    def archive_tasks(
        self,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> list[str]:
        archived_ids: list[str] = []
        repos = self._task_admin_repositories()
        for task in repos.task_repo.get_all():
            item = task.model_dump()
            if not self.task_matches_filters(
                item,
                statuses=statuses,
                team_id=team_id,
                before_ts=before_ts,
                task_ids=task_ids,
            ):
                continue
            removed, _snapshot = self._remove_active_task(
                task_id=item["id"],
                archive=True,
                vector_authorization=vector_authorization,
            )
            if removed:
                archived_ids.append(item["id"])
        return archived_ids

    def restore_task(
        self,
        *,
        task_id: str,
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> bool:
        return self._mutate_archived_task(
            task_id=task_id,
            action="restore",
            vector_authorization=vector_authorization,
        )

    def restore_tasks(
        self,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> list[str]:
        restored_ids: list[str] = []
        for archived in self.load_all_archived_tasks():
            if not self.task_matches_filters(
                archived,
                statuses=statuses,
                team_id=team_id,
                before_ts=before_ts,
                task_ids=task_ids,
            ):
                continue
            task_id = str(archived.get("id") or "")
            if self.restore_task(
                task_id=task_id,
                vector_authorization=vector_authorization,
            ):
                restored_ids.append(task_id)
        return restored_ids

    def delete_archived_task(
        self,
        *,
        task_id: str,
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> bool:
        return self._mutate_archived_task(
            task_id=task_id,
            action="delete",
            vector_authorization=vector_authorization,
        )

    def cleanup_archived_tasks(
        self,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> tuple[list[str], list[dict]]:
        deleted_ids: list[str] = []
        errors: list[dict] = []
        for item in self.load_all_archived_tasks():
            if not self.task_matches_filters(
                item,
                statuses=statuses,
                team_id=team_id,
                before_ts=before_ts,
                task_ids=task_ids,
            ):
                continue
            task_id = item.get("id")
            try:
                if self.delete_archived_task(
                    task_id=str(task_id or ""),
                    vector_authorization=vector_authorization,
                ):
                    deleted_ids.append(task_id)
            except Exception as exc:
                errors.append(_batch_error(task_id, exc))
        return deleted_ids, errors

    def apply_archive_retention(
        self,
        *,
        team_id: str,
        statuses: set[str],
        cutoff: float,
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> list[str]:
        deleted_ids, _errors = self.apply_archive_retention_with_errors(
            team_id=team_id,
            statuses=statuses,
            cutoff=cutoff,
            vector_authorization=vector_authorization,
        )
        return deleted_ids

    def apply_archive_retention_with_errors(
        self,
        *,
        team_id: str,
        statuses: set[str],
        cutoff: float,
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        deleted_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        for item in self.load_all_archived_tasks():
            archived_at = float(
                item.get("archived_at")
                or item.get("updated_at")
                or 0
            )
            if archived_at >= cutoff:
                continue
            if team_id and (item.get("team_id") or "") != team_id:
                continue
            if (
                statuses
                and normalize_task_status(
                    item.get("status"),
                    default="",
                )
                not in statuses
            ):
                continue
            task_id = str(item.get("id") or "")
            try:
                if self._mutate_archived_task(
                    task_id=task_id,
                    action="retention",
                    vector_authorization=vector_authorization,
                ):
                    deleted_ids.append(task_id)
            except Exception as exc:
                errors.append(_batch_error(task_id, exc))
        return deleted_ids, errors

    def cleanup_active_tasks(
        self,
        *,
        mode: str,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
        vector_authorization: VectorAdminAuthorizationContext | None = None,
    ) -> tuple[list[dict], list[str], list[str], list[dict]]:
        repos = self._task_admin_repositories()
        matched = [
            item
            for task in repos.task_repo.get_all()
            for item in [task.model_dump()]
            if self.task_matches_filters(
                item,
                statuses=statuses,
                team_id=team_id,
                before_ts=before_ts,
                task_ids=task_ids,
            )
        ]
        archived_ids: list[str] = []
        deleted_ids: list[str] = []
        errors: list[dict] = []
        for item in matched:
            task_id = item.get("id")
            try:
                removed, _snapshot = self._remove_active_task(
                    task_id=str(task_id or ""),
                    archive=mode == "archive",
                    vector_authorization=vector_authorization,
                )
                if not removed:
                    continue
                if mode == "archive":
                    archived_ids.append(task_id)
                else:
                    deleted_ids.append(task_id)
            except Exception as exc:
                errors.append(_batch_error(task_id, exc))
        return matched, archived_ids, deleted_ids, errors


__all__ = [
    "RecoveryChildAdminMutationConflict",
    "TaskArchiveAdminMixin",
]
