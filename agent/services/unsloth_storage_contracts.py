"""Stable storage-governance value objects and ports.

These contracts keep Hub-side policy independent from SQLite persistence details.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

ARTIFACT_KINDS = frozenset({"dataset", "model", "checkpoint", "export", "workspace"})
REFERENCE_KINDS = frozenset({"evaluation", "promotion", "endpoint"})


class UnslothStorageError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class UnslothStorageQuotaPolicy:
    max_dataset_bytes: int = 4 * 1024**3
    max_model_bytes: int = 20 * 1024**3
    max_checkpoint_bytes: int = 8 * 1024**3
    max_export_bytes: int = 20 * 1024**3
    max_tenant_bytes: int = 64 * 1024**3
    retention_seconds: int = 30 * 24 * 60 * 60
    max_cleanup_items: int = 128

    def __post_init__(self) -> None:
        limits = (
            self.max_dataset_bytes,
            self.max_model_bytes,
            self.max_checkpoint_bytes,
            self.max_export_bytes,
            self.max_tenant_bytes,
            self.retention_seconds,
            self.max_cleanup_items,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in limits):
            raise ValueError("Unsloth storage limits must be positive integers")
        if self.max_tenant_bytes < max(limits[:4]):
            raise ValueError("max_tenant_bytes must cover every individual storage quota")
        if self.max_cleanup_items > 1024:
            raise ValueError("max_cleanup_items exceeds its safe bound")

    def kind_limit(self, kind: str) -> int:
        return {
            "dataset": self.max_dataset_bytes,
            "model": self.max_model_bytes,
            "checkpoint": self.max_checkpoint_bytes,
            "export": self.max_export_bytes,
            "workspace": self.max_tenant_bytes,
        }[kind]

    def public_summary(self) -> dict[str, int]:
        return {
            "dataset_bytes": self.max_dataset_bytes,
            "model_bytes": self.max_model_bytes,
            "checkpoint_bytes": self.max_checkpoint_bytes,
            "export_bytes": self.max_export_bytes,
            "tenant_total_bytes": self.max_tenant_bytes,
            "retention_seconds": self.retention_seconds,
            "max_cleanup_items": self.max_cleanup_items,
        }


@dataclass(frozen=True, slots=True)
class StorageArtifactRecord:
    tenant_id: str
    owner_scope_digest: str
    artifact_id: str
    kind: str
    relative_ref: str
    job_id: str
    attempt_id: str
    sha256: str
    size_bytes: int
    created_at: float
    retention_until: float
    state: str = "active"
    cleanup_task_id: str | None = None

    def public_summary(
        self,
        *,
        reference_kinds: Sequence[str] = (),
    ) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "storage_ref": f"unsloth-storage:{self.artifact_id}",
            "kind": self.kind,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "retention_until": self.retention_until,
            "state": self.state,
            "reference_kinds": sorted(set(reference_kinds)),
            "referenced": bool(reference_kinds),
            "cleanup_task_id": self.cleanup_task_id,
        }


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    tenant_id: str
    owner_scope_digest: str
    catalog_revision: int
    artifact_ids: tuple[str, ...]
    delegations: tuple[Mapping[str, Any], ...]
    protected: tuple[Mapping[str, Any], ...]
    total_bytes: int
    plan_sha256: str

    def public_summary(self) -> dict[str, Any]:
        return {
            "catalog_revision": self.catalog_revision,
            "candidate_artifact_ids": list(self.artifact_ids),
            "candidate_count": len(self.artifact_ids),
            "candidate_bytes": self.total_bytes,
            "protected": [dict(item) for item in self.protected],
            "protected_count": len(self.protected),
            "plan_sha256": self.plan_sha256,
            "paths_exposed": False,
        }


class StorageReferencePort(Protocol):
    def bind_reference(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        artifact_sha256: str,
        reference_kind: str,
        reference_id: str,
    ) -> None: ...


class StorageCleanupCompletionPort(Protocol):
    def mark_cleanup_completed(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        task_id: str,
        artifacts: Sequence[Mapping[str, Any]],
    ) -> int: ...


class StorageCleanupAdmissionPort(Protocol):
    def mark_cleanup_queued(
        self,
        *,
        plan: CleanupPlan,
        task_id: str,
    ) -> int: ...

    def release_cleanup_queued(
        self,
        *,
        plan: CleanupPlan,
        task_id: str,
    ) -> int: ...


class UnslothStorageCleanupCatalogPort(
    StorageReferencePort,
    StorageCleanupAdmissionPort,
    StorageCleanupCompletionPort,
    Protocol,
):
    """Persistence-neutral catalog operations consumed by the Hub cleanup command."""

    policy: UnslothStorageQuotaPolicy

    def usage(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str | None = None,
    ) -> dict[str, Any]: ...

    def plan_cleanup(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        artifact_ids: Sequence[str],
        expected_catalog_revision: int,
        retention_before: float | None = None,
    ) -> CleanupPlan: ...
