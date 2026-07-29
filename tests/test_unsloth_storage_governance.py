from __future__ import annotations

import hashlib
from typing import Any, Mapping

import pytest

from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_storage_governance_service import (
    SqliteUnslothStorageCatalog,
    UnslothStorageCleanupMutationExecutor,
    UnslothStorageError,
    UnslothStorageQuotaPolicy,
    tenant_scope_digest,
)
from agent.services.unsloth_task_port import derive_unsloth_task_id


PRINCIPAL = MlInternTrainingPrincipal(
    tenant_id="tenant-a",
    subject="owner-a",
)


def _catalog(tmp_path, *, max_export_bytes: int = 1024):
    return SqliteUnslothStorageCatalog(
        tmp_path / "storage.sqlite3",
        policy=UnslothStorageQuotaPolicy(
            max_dataset_bytes=1024,
            max_model_bytes=1024,
            max_checkpoint_bytes=1024,
            max_export_bytes=max_export_bytes,
            max_tenant_bytes=4096,
            retention_seconds=60,
            max_cleanup_items=16,
        ),
        clock=lambda: 1_000.0,
    )


def _register_export(catalog, *, artifact_id: str = "export-a", size_bytes: int = 7):
    scope = tenant_scope_digest(PRINCIPAL)
    return catalog.register(
        tenant_id="tenant-a",
        owner_scope_digest=scope,
        artifact_id=artifact_id,
        kind="export",
        relative_ref=(
            f"tenants/{scope}/jobs/job-a/attempts/attempt-a/"
            f"exports/{artifact_id}.zip"
        ),
        job_id="job-a",
        attempt_id="attempt-a",
        artifact_sha256=hashlib.sha256(artifact_id.encode()).hexdigest(),
        size_bytes=size_bytes,
        created_at=900.0,
    )


def test_catalog_enforces_quota_scope_and_path_free_public_projection(tmp_path):
    catalog = _catalog(tmp_path, max_export_bytes=8)
    _register_export(catalog)

    scope = tenant_scope_digest(PRINCIPAL)
    usage = catalog.usage(
        tenant_id="tenant-a",
        owner_scope_digest=scope,
    )
    artifacts = catalog.list_public(
        tenant_id="tenant-a",
        owner_scope_digest=scope,
    )

    assert usage["usage"]["export"]["bytes"] == 7
    assert usage["tenant_total_bytes"] == 7
    assert len(artifacts) == 1
    assert "relative_ref" not in artifacts[0]
    assert not any(
        isinstance(value, str) and value.startswith("/")
        for value in artifacts[0].values()
    )

    with pytest.raises(UnslothStorageError):
        _register_export(catalog, artifact_id="export-over-quota", size_bytes=2)

    with pytest.raises(UnslothStorageError):
        catalog.register(
            tenant_id="tenant-a",
            owner_scope_digest=scope,
            artifact_id="escaped",
            kind="export",
            relative_ref="../escaped.zip",
            job_id="job-a",
            attempt_id="attempt-a",
            artifact_sha256="0" * 64,
            size_bytes=1,
        )


def test_cleanup_plan_is_revision_fenced_and_protects_immutable_references(tmp_path):
    catalog = _catalog(tmp_path)
    record = _register_export(catalog)
    catalog.bind_reference(
        tenant_id="tenant-a",
        reference_kind="evaluation",
        reference_id="evaluation-a",
        artifact_id=record.artifact_id,
        artifact_sha256=record.sha256,
    )
    scope = tenant_scope_digest(PRINCIPAL)
    usage = catalog.usage(
        tenant_id="tenant-a",
        owner_scope_digest=scope,
    )

    plan = catalog.plan_cleanup(
        tenant_id="tenant-a",
        owner_scope_digest=scope,
        artifact_ids=[record.artifact_id],
        expected_catalog_revision=usage["catalog_revision"],
        retention_before=950.0,
    )

    public = plan.public_summary()
    assert public["candidate_count"] == 0
    assert public["protected_count"] == 1
    assert "relative_ref" not in str(public)

    with pytest.raises(UnslothStorageError):
        catalog.plan_cleanup(
            tenant_id="tenant-a",
            owner_scope_digest=scope,
            artifact_ids=[record.artifact_id],
            expected_catalog_revision=usage["catalog_revision"] - 1,
            retention_before=950.0,
        )


class _CrashableReservedTasks:
    def __init__(
        self,
        *,
        crash_after_reserve: bool = False,
        crash_before_activate: bool = False,
    ) -> None:
        self.submission: dict[str, object] | None = None
        self.crash_after_reserve = crash_after_reserve
        self.crash_before_activate = crash_before_activate

    def reserve(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        task_id = derive_unsloth_task_id(
            tenant_id=tenant_id,
            task_type=task_type,
            idempotency_key=idempotency_key,
        )
        self.submission = {
            "task_id": task_id,
            "status": "reserved",
            "task_type": task_type,
            "tenant_id": tenant_id,
            "payload": dict(payload),
            "payload_sha256": "unused-in-domain-reconcile",
            "result_handler": "unsloth_storage_cleanup_v1",
        }
        if self.crash_after_reserve:
            self.crash_after_reserve = False
            raise RuntimeError("simulated_crash_after_reserve")
        return task_id

    def get_submission(
        self,
        task_id: str,
    ) -> Mapping[str, object] | None:
        if (
            self.submission is None
            or self.submission["task_id"] != task_id
        ):
            return None
        return dict(self.submission)

    def activate_reserved(self, task_id: str) -> bool:
        assert self.submission is not None
        assert self.submission["task_id"] == task_id
        if self.crash_before_activate:
            self.crash_before_activate = False
            raise RuntimeError("simulated_crash_before_activate")
        self.submission["status"] = "created"
        return True

    def reject_reserved(
        self,
        task_id: str,
        *,
        reason_code: str,
    ) -> bool:
        assert self.submission is not None
        assert self.submission["task_id"] == task_id
        self.submission["status"] = "cancelled"
        self.submission["reason_code"] = reason_code
        return True


def _cleanup_operation(catalog) -> dict[str, Any]:
    usage = catalog.usage(
        tenant_id=PRINCIPAL.tenant_id,
        owner_scope_digest=tenant_scope_digest(PRINCIPAL),
    )
    return {
        "artifact_ids": ["export-a"],
        "expected_catalog_revision": usage["catalog_revision"],
        "retention_before": 950.0,
    }


def _execute_cleanup(executor, operation):
    return executor.execute_operation(
        principal=PRINCIPAL,
        resource_id="cleanup-a",
        reason="retention elapsed",
        idempotency_key="cleanup-idempotency-a",
        operation_payload=operation,
    )


def test_cleanup_reconciles_crash_after_reservation_before_catalog_cas(
    tmp_path,
):
    catalog = _catalog(tmp_path)
    _register_export(catalog)
    operation = _cleanup_operation(catalog)
    tasks = _CrashableReservedTasks(crash_after_reserve=True)
    executor = UnslothStorageCleanupMutationExecutor(
        catalog=catalog,
        tasks=tasks,
    )

    with pytest.raises(
        RuntimeError,
        match="simulated_crash_after_reserve",
    ):
        _execute_cleanup(executor, operation)

    result = _execute_cleanup(executor, operation)

    assert result["status"] == "queued"
    assert result["replayed"] is True
    assert tasks.submission is not None
    assert tasks.submission["status"] == "created"


def test_cleanup_reconciles_crash_after_catalog_cas_before_activation(
    tmp_path,
):
    catalog = _catalog(tmp_path)
    _register_export(catalog)
    operation = _cleanup_operation(catalog)
    tasks = _CrashableReservedTasks(crash_before_activate=True)
    executor = UnslothStorageCleanupMutationExecutor(
        catalog=catalog,
        tasks=tasks,
    )

    with pytest.raises(
        RuntimeError,
        match="simulated_crash_before_activate",
    ):
        _execute_cleanup(executor, operation)
    revision_after_cas = catalog.usage(
        tenant_id=PRINCIPAL.tenant_id,
        owner_scope_digest=tenant_scope_digest(PRINCIPAL),
    )["catalog_revision"]

    result = _execute_cleanup(executor, operation)

    assert result["catalog_revision_after"] == revision_after_cas
    assert tasks.submission is not None
    assert tasks.submission["status"] == "created"
