"""Hub-owned projection of a capability-free knowledge-index base task."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from flask import current_app

from agent.services.repository_registry import get_repository_registry
from agent.services.task_mutation_lock_service import (
    get_task_mutation_lock_port,
)
from ananta_contracts.knowledge_index_task_snapshot import (
    build_knowledge_index_task_snapshot,
)

_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }
)


@dataclass
class KnowledgeIndexTaskSnapshotDenied(RuntimeError):
    reason_code: str
    status_code: int

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.reason_code)


def _binding_service() -> Any:
    service = current_app.extensions.get("knowledge_index_execution_binding_service")
    if service is None:
        raise KnowledgeIndexTaskSnapshotDenied(
            "knowledge_index_task_snapshot_service_unavailable",
            503,
        )
    return service


class KnowledgeIndexTaskSnapshotService:
    """Validate current Hub authority and project one minimal Worker task."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] = get_repository_registry,
        binding_service_provider: Callable[[], Any] = _binding_service,
        lock_provider: Callable[[], Any] = get_task_mutation_lock_port,
    ) -> None:
        self._repository_provider = repository_provider
        self._binding_service_provider = binding_service_provider
        self._lock_provider = lock_provider

    def snapshot_for_worker(
        self,
        *,
        task_id: str,
        worker_id: str,
        worker_url: str,
    ) -> dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        normalized_worker_id = str(worker_id or "").strip()
        normalized_worker_url = str(worker_url or "").strip().rstrip("/")
        if not normalized_task_id or not normalized_worker_id or not normalized_worker_url:
            self._deny("knowledge_index_task_snapshot_identity_invalid", 403)

        with self._lock_provider().mutation_lock(normalized_task_id) as acquired:
            if not acquired:
                self._deny(
                    "knowledge_index_task_snapshot_lock_unavailable",
                    503,
                )
            task = self._repository_provider().task_repo.get_by_id(normalized_task_id)
            if task is None or str(self._value(task, "task_kind") or "").strip().lower() != "codecompass_index_build":
                self._deny(
                    "knowledge_index_task_snapshot_not_found",
                    404,
                )
            assigned_url = str(self._value(task, "assigned_agent_url") or "").strip().rstrip("/")
            if assigned_url != normalized_worker_url:
                self._deny(
                    "knowledge_index_task_snapshot_assignment_denied",
                    404,
                )
            status = str(self._value(task, "status") or "").strip().lower()
            if not status or status in _TERMINAL_STATUSES:
                self._deny(
                    "knowledge_index_task_snapshot_task_terminal",
                    409,
                )
            context = self._mapping(self._value(task, "worker_execution_context"))
            stored_job = self._mapping(context.get("knowledge_index_job"))
            stored_job.pop(
                "source_access_enforcement_manifest",
                None,
            )
            try:
                current = self._binding_service_provider().validate_before_dispatch(
                    job_id=normalized_task_id,
                    authenticated_worker_id=normalized_worker_id,
                )
            except KnowledgeIndexTaskSnapshotDenied:
                raise
            except Exception as exc:
                reason = str(getattr(exc, "reason_code", "") or exc).strip()
                self._deny(
                    reason or "knowledge_index_task_snapshot_authority_invalid",
                    409,
                    cause=exc,
                )
            current_job = current.job.to_wire()
            if stored_job != current_job:
                self._deny(
                    "knowledge_index_task_snapshot_queue_context_stale",
                    409,
                )
            if current.job.assignment.worker_id != normalized_worker_id:
                self._deny(
                    "knowledge_index_task_snapshot_worker_mismatch",
                    404,
                )
            try:
                return build_knowledge_index_task_snapshot(
                    status=status,
                    job=current_job,
                    worker_id=normalized_worker_id,
                    worker_url=normalized_worker_url,
                )
            except ValueError as exc:
                self._deny(str(exc), 409, cause=exc)
        raise AssertionError("unreachable")

    @staticmethod
    def _value(source: Any, field: str) -> Any:
        if isinstance(source, Mapping):
            return source.get(field)
        return getattr(source, field, None)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _deny(
        reason_code: str,
        status_code: int,
        *,
        cause: Exception | None = None,
    ) -> None:
        error = KnowledgeIndexTaskSnapshotDenied(
            str(reason_code or "knowledge_index_task_snapshot_denied"),
            int(status_code),
        )
        if cause is not None:
            raise error from cause
        raise error


_SERVICE = KnowledgeIndexTaskSnapshotService()


def get_knowledge_index_task_snapshot_service() -> KnowledgeIndexTaskSnapshotService:
    return _SERVICE


__all__ = [
    "KnowledgeIndexTaskSnapshotDenied",
    "KnowledgeIndexTaskSnapshotService",
    "get_knowledge_index_task_snapshot_service",
]
