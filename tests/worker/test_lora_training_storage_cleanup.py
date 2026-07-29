from __future__ import annotations

import hashlib

import pytest

from ananta_contracts.unsloth_task import (
    build_unsloth_task_result,
    normalize_unsloth_cleanup_payload,
)
from worker.training.storage_cleanup import (
    WorkerStorageCleanupError,
    WorkerStorageCleanupExecutor,
)
from worker.training.unsloth_worker_runtime import (
    STORAGE_CLEANUP_MODE,
    build_unsloth_worker_runtime,
)


def _envelope(*, relative_ref: str, digest: str, size_bytes: int):
    return {
        "contract_version": "ananta.unsloth-storage-cleanup-task.v1",
        "task_id": "cleanup-task-a",
        "tenant_scope_digest": "a" * 64,
        "catalog_revision": 3,
        "plan_sha256": "b" * 64,
        "reason_sha256": "c" * 64,
        "artifacts": [
            {
                "artifact_id": "export-a",
                "kind": "export",
                "relative_ref": relative_ref,
                "job_id": "job-a",
                "attempt_id": "attempt-a",
                "sha256": digest,
                "size_bytes": size_bytes,
            }
        ],
    }


def test_cleanup_deletes_only_exact_delegated_tenant_attempt_path_and_replays(tmp_path):
    workspace_root = tmp_path / "workspace"
    state_root = tmp_path / "state"
    workspace_root.mkdir()
    state_root.mkdir()
    relative_ref = (
        f"tenants/{'a' * 64}/jobs/job-a/attempts/attempt-a/"
        "exports/export-a.zip"
    )
    artifact = state_root / relative_ref
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"verified-export")
    executor = WorkerStorageCleanupExecutor(
        workspace_root=workspace_root,
        state_root=state_root,
    )
    envelope = _envelope(
        relative_ref=relative_ref,
        digest=hashlib.sha256(b"verified-export").hexdigest(),
        size_bytes=len(b"verified-export"),
    )

    first = executor.execute(envelope)
    second = executor.execute(envelope)

    assert first["schema"] == (
        "ananta.unsloth-storage-cleanup-result.v1"
    )
    assert first["status"] == "completed"
    assert first["deleted_count"] == 1
    assert not artifact.exists()
    assert second == {**first, "replayed": True}
    assert "relative_ref" not in str(first)

    outer = build_unsloth_task_result(
        task_id=envelope["task_id"],
        task_type="ml.storage.cleanup",
        tenant_id="tenant-a",
        payload_sha256="d" * 64,
        status="completed",
        result=first,
    )
    assert outer["reason_code"] is None
    assert outer["result"] == first


@pytest.mark.parametrize(
    "relative_ref",
    [
        "/tmp/export-a.zip",
        "../export-a.zip",
        f"tenants/{'d' * 64}/jobs/job-a/attempts/attempt-a/exports/export-a.zip",
        f"tenants/{'a' * 64}/jobs/job-b/attempts/attempt-a/exports/export-a.zip",
        f"tenants/{'a' * 64}/jobs/job-a/attempts/attempt-b/exports/export-a.zip",
        f"tenants/{'a' * 64}/datasets/dataset-a/revision-1/train.jsonl",
    ],
)
def test_cleanup_rejects_paths_outside_delegated_scope(tmp_path, relative_ref):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "state").mkdir()
    executor = WorkerStorageCleanupExecutor(
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
    )
    with pytest.raises(WorkerStorageCleanupError):
        executor.execute(
            _envelope(relative_ref=relative_ref, digest="0" * 64, size_bytes=0)
        )


def test_cleanup_contract_is_closed_and_worker_profile_is_registered(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    payload = _envelope(
        relative_ref=(
            f"tenants/{'a' * 64}/jobs/job-a/attempts/"
            "attempt-a/exports/export-a.zip"
        ),
        digest="0" * 64,
        size_bytes=0,
    )

    assert normalize_unsloth_cleanup_payload(payload) == payload
    runtime = build_unsloth_worker_runtime(
        {
            "ANANTA_UNSLOTH_WORKER_MODE": STORAGE_CLEANUP_MODE,
            "ANANTA_UNSLOTH_STORAGE_CLEANUP_STATE_ROOT": str(state),
            "ANANTA_UNSLOTH_STORAGE_CLEANUP_WORKSPACE_ROOT": (
                str(workspace)
            ),
        }
    )

    assert runtime.ready is True
    assert runtime.bindings[0].task_kind == "ml.storage.cleanup"
    assert runtime.bindings[0].capabilities == (
        "unsloth_storage_cleanup",
    )
