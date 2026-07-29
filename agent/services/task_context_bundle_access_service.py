"""Task-bound access boundary for persisted retrieval context bundles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.repository_registry import get_repository_registry

CONTEXT_BUNDLE_REFERENCE_MISMATCH = "context_bundle_reference_mismatch"
CONTEXT_BUNDLE_TASK_ID_REQUIRED = "context_bundle_task_id_required"
CONTEXT_BUNDLE_TASK_MISMATCH = "context_bundle_task_mismatch"
CONTEXT_BUNDLE_TASK_UNBOUND = "context_bundle_task_unbound"
CONTEXT_BUNDLE_NOT_FOUND = "context_bundle_not_found"


class ContextBundleRepositoryPort(Protocol):
    """Small persistence port needed by the bundle access policy."""

    def get_by_id(self, bundle_id: str) -> Any: ...


class TaskContextBundleAccessPort(Protocol):
    """Consumer-facing task/bundle resolution contract."""

    def resolve_task_reference(
        self,
        *,
        task: Mapping[str, Any] | None,
        task_id: str | None = None,
    ) -> Any | None: ...

    def resolve_task_reference_or_none(
        self,
        *,
        task: Mapping[str, Any] | None,
        task_id: str | None = None,
    ) -> Any | None: ...


class ContextBundleTaskAccessError(ValueError):
    """Stable fail-closed error raised for an unsafe bundle reference."""

    def __init__(
        self,
        reason_code: str,
        *,
        bundle_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.bundle_id = bundle_id
        self.task_id = task_id


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


class TaskContextBundleAccessService:
    """Resolve bundles only when their persisted owner matches the Hub task."""

    def __init__(
        self,
        repository: ContextBundleRepositoryPort | None = None,
    ) -> None:
        self._repository = repository

    @property
    def _context_bundle_repository(self) -> ContextBundleRepositoryPort:
        return self._repository or get_repository_registry().context_bundle_repo

    @staticmethod
    def _referenced_bundle_id(task: Mapping[str, Any]) -> str | None:
        top_level = str(task.get("context_bundle_id") or "").strip()
        worker_context = task.get("worker_execution_context")
        nested = (
            str(worker_context.get("context_bundle_id") or "").strip() if isinstance(worker_context, Mapping) else ""
        )
        if top_level and nested and top_level != nested:
            raise ContextBundleTaskAccessError(
                CONTEXT_BUNDLE_REFERENCE_MISMATCH,
                bundle_id=nested,
                task_id=str(task.get("id") or "").strip() or None,
            )
        return nested or top_level or None

    def require_for_task(
        self,
        *,
        bundle_id: str,
        task_id: str,
    ) -> Any:
        normalized_bundle_id = str(bundle_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ContextBundleTaskAccessError(
                CONTEXT_BUNDLE_TASK_ID_REQUIRED,
                bundle_id=normalized_bundle_id or None,
            )
        bundle = self._context_bundle_repository.get_by_id(normalized_bundle_id) if normalized_bundle_id else None
        if bundle is None:
            raise ContextBundleTaskAccessError(
                CONTEXT_BUNDLE_NOT_FOUND,
                bundle_id=normalized_bundle_id or None,
                task_id=normalized_task_id,
            )
        bundle_task_id = str(_field(bundle, "task_id") or "").strip()
        if not bundle_task_id:
            raise ContextBundleTaskAccessError(
                CONTEXT_BUNDLE_TASK_UNBOUND,
                bundle_id=normalized_bundle_id,
                task_id=normalized_task_id,
            )
        if bundle_task_id != normalized_task_id:
            raise ContextBundleTaskAccessError(
                CONTEXT_BUNDLE_TASK_MISMATCH,
                bundle_id=normalized_bundle_id,
                task_id=normalized_task_id,
            )
        return bundle

    def resolve_task_reference(
        self,
        *,
        task: Mapping[str, Any] | None,
        task_id: str | None = None,
    ) -> Any | None:
        payload = task if isinstance(task, Mapping) else {}
        bundle_id = self._referenced_bundle_id(payload)
        if not bundle_id:
            return None
        effective_task_id = str(task_id or payload.get("id") or "").strip()
        return self.require_for_task(
            bundle_id=bundle_id,
            task_id=effective_task_id,
        )

    def resolve_task_reference_or_none(
        self,
        *,
        task: Mapping[str, Any] | None,
        task_id: str | None = None,
    ) -> Any | None:
        """Return no data for invalid references in non-execution read models."""

        try:
            return self.resolve_task_reference(task=task, task_id=task_id)
        except ContextBundleTaskAccessError:
            return None


_task_context_bundle_access_service = TaskContextBundleAccessService()


def get_task_context_bundle_access_service() -> TaskContextBundleAccessService:
    return _task_context_bundle_access_service


__all__ = [
    "CONTEXT_BUNDLE_NOT_FOUND",
    "CONTEXT_BUNDLE_REFERENCE_MISMATCH",
    "CONTEXT_BUNDLE_TASK_ID_REQUIRED",
    "CONTEXT_BUNDLE_TASK_MISMATCH",
    "CONTEXT_BUNDLE_TASK_UNBOUND",
    "ContextBundleRepositoryPort",
    "ContextBundleTaskAccessError",
    "TaskContextBundleAccessPort",
    "TaskContextBundleAccessService",
    "get_task_context_bundle_access_service",
]
