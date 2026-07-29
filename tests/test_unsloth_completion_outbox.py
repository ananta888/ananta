from __future__ import annotations

from pathlib import Path
from typing import Any

from ananta_contracts.unsloth_task import unsloth_payload_sha256
from agent.services.unsloth_completion_outbox_service import (
    UnslothCompletionOutboxReconciler,
)
from agent.services.unsloth_model_catalog_service import (
    ModelImportCompletionOutboxEntry,
    SqliteUnslothModelCatalogRegistry,
    UnslothModelImportResultHandler,
)


def _payload() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "source_id": "SRC_supplied-model",
        "kind": "local_artifact",
        "expected_sha256": "a" * 64,
        "artifact_id": "artifact-a",
        "model_id": None,
        "revision": None,
        "max_bytes": 1024,
        "allow_patterns": [],
        "trust_remote_code": False,
        "network_authorized": False,
        "license_status": "approved",
        "format": "safetensors",
        "architecture": "llama",
        "quantization": None,
        "capability_facets": ["training.text"],
    }


def _worker_result() -> dict[str, Any]:
    return {
        "schema": "ananta.unsloth-model-import-result.v1",
        "cache_key": "b" * 64,
        "relative_path": "b" * 64,
        "content_sha256": "a" * 64,
        "file_count": 1,
        "total_bytes": 10,
    }


def _task_and_envelope(
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _payload()
    payload_sha256 = unsloth_payload_sha256(payload)
    envelope = {
        "schema": "ananta.unsloth-worker-task-result.v1",
        "task_id": task_id,
        "task_type": "ml.model.import",
        "tenant_id": "tenant-a",
        "payload_sha256": payload_sha256,
        "status": "completed",
        "reason_code": None,
        "result": _worker_result(),
    }
    task = {
        "id": task_id,
        "status": "assigned",
        "verification_status": {},
        "worker_execution_context": {
            "schema": (
                "ananta.unsloth-worker-task-context.v1"
            ),
            "unsloth_task": {
                "task_type": "ml.model.import",
                "tenant_id": "tenant-a",
                "payload": payload,
                "payload_sha256": payload_sha256,
                "result_handler": "unsloth_model_import_v1",
                "followup_task_creation_allowed": False,
            },
        },
    }
    return task, envelope


class _Tasks:
    def __init__(
        self,
        task: dict[str, Any],
        *,
        fail_if_committed: bool = False,
    ) -> None:
        self.task = task
        self.commit_calls = 0
        self.fail_if_committed = fail_if_committed

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.task if self.task["id"] == task_id else None

    def commit_completion(
        self,
        entry: ModelImportCompletionOutboxEntry,
    ) -> bool:
        if self.fail_if_committed:
            raise AssertionError(
                "already committed replay must not rewrite task"
            )
        self.commit_calls += 1
        self.task["status"] = "completed"
        self.task["verification_status"] = {
            **dict(
                self.task.get("verification_status")
                or {}
            ),
            **entry.projection,
        }
        return True


def _prepare_pending(
    tmp_path: Path,
    task_id: str,
) -> tuple[
    SqliteUnslothModelCatalogRegistry,
    ModelImportCompletionOutboxEntry,
    dict[str, Any],
]:
    registry = SqliteUnslothModelCatalogRegistry(
        tmp_path / "catalog.sqlite3"
    )
    task, envelope = _task_and_envelope(task_id)
    _record, entry = UnslothModelImportResultHandler(
        registry
    ).handle_with_completion_outbox(
        task_id=task_id,
        task_payload=_payload(),
        worker_result=_worker_result(),
        worker_envelope=envelope,
    )
    return registry, entry, task


def test_pending_outbox_recovers_task_before_catalog_publish(
    tmp_path: Path,
) -> None:
    task_id = "unsloth-" + "5" * 32
    registry, entry, task = _prepare_pending(
        tmp_path,
        task_id,
    )
    tasks = _Tasks(task)

    assert registry.list_versions(
        tenant_id="tenant-a"
    ) == ()
    assert entry.state == "pending"

    reconciler = UnslothCompletionOutboxReconciler(
        outbox=registry,
        tasks=tasks,
    )
    assert reconciler.reconcile_task(task_id) is True

    assert task["status"] == "completed"
    assert tasks.commit_calls == 1
    assert len(
        registry.list_versions(tenant_id="tenant-a")
    ) == 1
    stored = registry.get_completion_outbox(
        task_id=task_id
    )
    assert stored is not None
    assert stored.state == "terminalized"


def test_replay_acknowledges_committed_task_without_rewrite(
    tmp_path: Path,
) -> None:
    task_id = "unsloth-" + "6" * 32
    registry, entry, task = _prepare_pending(
        tmp_path,
        task_id,
    )
    task["status"] = "completed"
    task["verification_status"] = dict(entry.projection)
    tasks = _Tasks(task, fail_if_committed=True)

    reconciler = UnslothCompletionOutboxReconciler(
        outbox=registry,
        tasks=tasks,
    )
    assert reconciler.reconcile_task(task_id) is True
    assert tasks.commit_calls == 0

    _record, replayed = UnslothModelImportResultHandler(
        registry
    ).handle_with_completion_outbox(
        task_id=task_id,
        task_payload=_payload(),
        worker_result=_worker_result(),
        worker_envelope=_task_and_envelope(task_id)[1],
    )
    assert replayed.outbox_id == entry.outbox_id
    assert len(
        registry.list_versions(tenant_id="tenant-a")
    ) == 1
