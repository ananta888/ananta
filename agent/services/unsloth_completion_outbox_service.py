"""Hub-owned reconciliation for transactional model-import completions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Protocol

from flask import current_app, has_app_context

from agent.services.unsloth_model_catalog_service import (
    ModelImportCompletionOutboxEntry,
    SqliteUnslothModelCatalogRegistry,
    get_unsloth_model_catalog_registry,
)


_RECONCILABLE_STATUSES = frozenset(
    {
        "created",
        "todo",
        "blocked",
        "blocked_by_dependency",
        "assigned",
        "in_progress",
        "running",
        "failed",
        "completed",
    }
)


class ModelImportCompletionOutboxPort(Protocol):
    def get_completion_outbox(
        self,
        *,
        task_id: str,
    ) -> ModelImportCompletionOutboxEntry | None: ...

    def list_pending_completion_outbox(
        self,
        *,
        limit: int,
    ) -> tuple[ModelImportCompletionOutboxEntry, ...]: ...

    def mark_completion_outbox_terminalized(
        self,
        *,
        outbox_id: str,
    ) -> None: ...

    def record_completion_outbox_failure(
        self,
        *,
        outbox_id: str,
        reason_code: str,
    ) -> None: ...


class ModelImportCompletionTaskPort(Protocol):
    def get_task(self, task_id: str) -> Any | None: ...

    def commit_completion(
        self,
        entry: ModelImportCompletionOutboxEntry,
    ) -> bool: ...


class HubModelImportCompletionTaskAdapter:
    """CAS one authoritative Hub Task; never dispatch Worker work."""

    def __init__(
        self,
        *,
        repository: Any | None = None,
        compare_and_set: Callable[..., bool] | None = None,
    ) -> None:
        self._repository = repository
        self._compare_and_set = compare_and_set

    def get_task(self, task_id: str) -> Any | None:
        return self._task_repository().get_by_id(
            str(task_id or "")
        )

    def commit_completion(
        self,
        entry: ModelImportCompletionOutboxEntry,
    ) -> bool:
        task = self.get_task(entry.task_id)
        if (
            task is None
            or not _task_matches_entry(task, entry)
        ):
            return False
        verification = dict(
            _task_value(task, "verification_status")
            or {}
        )
        for key, expected in entry.projection.items():
            existing = verification.get(key)
            if existing is not None and existing != expected:
                return False
            verification[key] = expected
        return bool(
            self._status_updater()(
                entry.task_id,
                "completed",
                expected_statuses=_RECONCILABLE_STATUSES,
                authoritative_predicate=(
                    lambda candidate: _task_matches_entry(
                        candidate,
                        entry,
                    )
                ),
                event_type=(
                    "unsloth_model_import_completion_"
                    "reconciled"
                ),
                event_actor="hub:unsloth-outbox",
                event_details={
                    "outbox_id": entry.outbox_id,
                    "catalog_revision": (
                        entry.catalog_revision
                    ),
                },
                force=True,
                verification_status=verification,
            )
        )

    def _task_repository(self) -> Any:
        if self._repository is None:
            from agent.repository import task_repo

            self._repository = task_repo
        return self._repository

    def _status_updater(self) -> Callable[..., bool]:
        if self._compare_and_set is None:
            from agent.services.task_runtime_service import (
                compare_and_set_local_task_status,
            )

            self._compare_and_set = (
                compare_and_set_local_task_status
            )
        return self._compare_and_set


class UnslothCompletionOutboxReconciler:
    """Converge a committed Catalog/Outbox pair with its Hub Task."""

    def __init__(
        self,
        *,
        outbox: ModelImportCompletionOutboxPort,
        tasks: ModelImportCompletionTaskPort,
    ) -> None:
        self._outbox = outbox
        self._tasks = tasks

    def reconcile_task(self, task_id: str) -> bool:
        entry = self._outbox.get_completion_outbox(
            task_id=str(task_id or "")
        )
        if entry is None:
            return False
        if entry.state == "terminalized":
            return True
        task = self._tasks.get_task(entry.task_id)
        if task is None or not _task_matches_entry(task, entry):
            self._record_failure(
                entry,
                "model_import_completion_task_binding_invalid",
            )
            return False
        if not _task_has_projection(task, entry):
            if not self._tasks.commit_completion(entry):
                latest = self._tasks.get_task(entry.task_id)
                if (
                    latest is None
                    or not _task_has_projection(latest, entry)
                ):
                    self._record_failure(
                        entry,
                        (
                            "model_import_completion_"
                            "task_cas_failed"
                        ),
                    )
                    return False
        self._outbox.mark_completion_outbox_terminalized(
            outbox_id=entry.outbox_id
        )
        return True

    def reconcile_pending(
        self,
        *,
        limit: int = 100,
    ) -> dict[str, int]:
        entries = self._outbox.list_pending_completion_outbox(
            limit=max(1, min(int(limit), 1000))
        )
        reconciled = 0
        failed = 0
        for entry in entries:
            try:
                if self.reconcile_task(entry.task_id):
                    reconciled += 1
                else:
                    failed += 1
            except Exception:  # noqa: BLE001 - persistent retry boundary
                self._record_failure(
                    entry,
                    (
                        "model_import_completion_"
                        "reconciliation_failed"
                    ),
                )
                failed += 1
        return {
            "pending": len(entries),
            "reconciled": reconciled,
            "failed": failed,
        }

    def _record_failure(
        self,
        entry: ModelImportCompletionOutboxEntry,
        reason_code: str,
    ) -> None:
        self._outbox.record_completion_outbox_failure(
            outbox_id=entry.outbox_id,
            reason_code=reason_code,
        )


def _task_has_projection(
    task: Any,
    entry: ModelImportCompletionOutboxEntry,
) -> bool:
    if str(_task_value(task, "status") or "").strip().lower() != (
        "completed"
    ):
        return False
    verification = _task_value(
        task,
        "verification_status",
    )
    return bool(
        isinstance(verification, Mapping)
        and all(
            verification.get(key) == expected
            for key, expected in entry.projection.items()
        )
    )


def _task_matches_entry(
    task: Any,
    entry: ModelImportCompletionOutboxEntry,
) -> bool:
    if str(_task_value(task, "id") or "") != entry.task_id:
        return False
    context = _task_value(
        task,
        "worker_execution_context",
    )
    if (
        not isinstance(context, Mapping)
        or context.get("schema")
        != "ananta.unsloth-worker-task-context.v1"
    ):
        return False
    envelope = context.get("unsloth_task")
    worker_result = entry.projection.get(
        "unsloth_worker_result"
    )
    return bool(
        isinstance(envelope, Mapping)
        and isinstance(worker_result, Mapping)
        and envelope.get("task_type")
        == "ml.model.import"
        and envelope.get("result_handler")
        == "unsloth_model_import_v1"
        and envelope.get("tenant_id")
        == worker_result.get("tenant_id")
        and envelope.get("payload_sha256")
        == worker_result.get("payload_sha256")
        and worker_result.get("task_id")
        == entry.task_id
        and worker_result.get("task_type")
        == "ml.model.import"
        and worker_result.get("status")
        == "completed"
    )


def _task_value(task: Any, name: str) -> Any:
    if isinstance(task, Mapping):
        return task.get(name)
    return getattr(task, name, None)


def get_unsloth_completion_outbox_reconciler(
) -> UnslothCompletionOutboxReconciler:
    if not has_app_context():
        raise RuntimeError(
            "unsloth_completion_outbox_app_context_required"
        )
    configured = current_app.extensions.get(
        "unsloth_completion_outbox_reconciler"
    )
    if isinstance(
        configured,
        UnslothCompletionOutboxReconciler,
    ):
        return configured
    registry: SqliteUnslothModelCatalogRegistry = (
        get_unsloth_model_catalog_registry()
    )
    reconciler = UnslothCompletionOutboxReconciler(
        outbox=registry,
        tasks=HubModelImportCompletionTaskAdapter(),
    )
    current_app.extensions[
        "unsloth_completion_outbox_reconciler"
    ] = reconciler
    return reconciler


__all__ = [
    "HubModelImportCompletionTaskAdapter",
    "ModelImportCompletionOutboxPort",
    "ModelImportCompletionTaskPort",
    "UnslothCompletionOutboxReconciler",
    "get_unsloth_completion_outbox_reconciler",
]
