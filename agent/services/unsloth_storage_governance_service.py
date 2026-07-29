"""Hub-owned storage quotas, references, retention, and cleanup admission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import threading
import time
from typing import Any, Protocol

from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_mutation_command_service import (
    UnslothMutationError,
)
from agent.services.unsloth_task_port import (
    HubTaskSubmissionPort,
    derive_unsloth_task_id,
)


_ARTIFACT_KINDS = frozenset(
    {"dataset", "model", "checkpoint", "export", "workspace"}
)
_REFERENCE_KINDS = frozenset({"evaluation", "promotion", "endpoint"})
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in limits
        ):
            raise ValueError("Unsloth storage limits must be positive integers")
        if self.max_tenant_bytes < max(limits[:4]):
            raise ValueError(
                "max_tenant_bytes must cover every individual storage quota"
            )
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


class SqliteUnslothStorageCatalog(StorageReferencePort):
    """Persistent tenant catalog; local paths never enter public projections."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS unsloth_storage_artifacts (
            tenant_id TEXT NOT NULL,
            owner_scope_digest TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            relative_ref TEXT NOT NULL,
            job_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at REAL NOT NULL,
            retention_until REAL NOT NULL,
            state TEXT NOT NULL,
            cleanup_task_id TEXT,
            PRIMARY KEY (tenant_id, artifact_id)
        );
        CREATE TABLE IF NOT EXISTS unsloth_storage_references (
            tenant_id TEXT NOT NULL,
            reference_kind TEXT NOT NULL,
            reference_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, reference_kind, reference_id),
            FOREIGN KEY (tenant_id, artifact_id)
              REFERENCES unsloth_storage_artifacts (tenant_id, artifact_id)
        );
        CREATE TABLE IF NOT EXISTS unsloth_storage_revisions (
            tenant_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL
        );
    """

    def __init__(
        self,
        path: str | Path,
        *,
        policy: UnslothStorageQuotaPolicy | None = None,
        clock=time.time,
    ) -> None:
        self._path = Path(path)
        self.policy = policy or UnslothStorageQuotaPolicy()
        self._clock = clock
        self._initialization_lock = threading.Lock()
        self._initialize()

    def register(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        artifact_id: str,
        kind: str,
        relative_ref: str,
        job_id: str,
        attempt_id: str,
        artifact_sha256: str,
        size_bytes: int,
        created_at: float | None = None,
    ) -> StorageArtifactRecord:
        tenant = _tenant(tenant_id)
        owner_scope = _digest(owner_scope_digest, "storage_scope_digest_invalid")
        identifier = _opaque(artifact_id, "storage_artifact_id_invalid")
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in _ARTIFACT_KINDS:
            raise UnslothStorageError(
                "storage_artifact_kind_invalid",
                "Storage artifact kind is unsupported.",
                status_code=422,
            )
        job = _opaque(job_id, "storage_job_id_invalid")
        attempt = _opaque(attempt_id, "storage_attempt_id_invalid")
        digest = _digest(artifact_sha256, "storage_artifact_hash_invalid")
        size = _bounded_size(size_bytes)
        relative = _scoped_relative_ref(
            tenant_id=tenant,
            owner_scope_digest=owner_scope,
            kind=normalized_kind,
            relative_ref=relative_ref,
            job_id=job,
            attempt_id=attempt,
        )
        now = float(self._clock() if created_at is None else created_at)
        if not 0 <= now <= 2**63:
            raise UnslothStorageError(
                "storage_created_at_invalid",
                "Storage creation time is invalid.",
                status_code=422,
            )
        record = StorageArtifactRecord(
            tenant_id=tenant,
            owner_scope_digest=owner_scope,
            artifact_id=identifier,
            kind=normalized_kind,
            relative_ref=relative,
            job_id=job,
            attempt_id=attempt,
            sha256=digest,
            size_bytes=size,
            created_at=now,
            retention_until=now + self.policy.retention_seconds,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT tenant_id, owner_scope_digest, artifact_id, kind,
                       relative_ref, job_id, attempt_id, artifact_sha256,
                       size_bytes, created_at, retention_until, state,
                       cleanup_task_id
                FROM unsloth_storage_artifacts
                WHERE tenant_id = ? AND artifact_id = ?
                """,
                (tenant, identifier),
            ).fetchone()
            if existing is not None:
                current = _artifact_record(existing)
                immutable = (
                    current.owner_scope_digest,
                    current.kind,
                    current.relative_ref,
                    current.job_id,
                    current.attempt_id,
                    current.sha256,
                    current.size_bytes,
                )
                requested = (
                    record.owner_scope_digest,
                    record.kind,
                    record.relative_ref,
                    record.job_id,
                    record.attempt_id,
                    record.sha256,
                    record.size_bytes,
                )
                if immutable != requested:
                    raise UnslothStorageError(
                        "storage_artifact_id_conflict",
                        "Artifact ID is already bound to different immutable storage.",
                    )
                return current
            kind_used = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(size_bytes), 0)
                    FROM unsloth_storage_artifacts
                    WHERE tenant_id = ? AND kind = ?
                      AND state IN ('active', 'cleanup_queued')
                    """,
                    (tenant, normalized_kind),
                ).fetchone()[0]
            )
            tenant_used = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(size_bytes), 0)
                    FROM unsloth_storage_artifacts
                    WHERE tenant_id = ?
                      AND state IN ('active', 'cleanup_queued')
                    """,
                    (tenant,),
                ).fetchone()[0]
            )
            if kind_used + size > self.policy.kind_limit(normalized_kind):
                raise UnslothStorageError(
                    f"storage_{normalized_kind}_quota_exceeded",
                    f"{normalized_kind} storage exceeds its configured quota.",
                    status_code=413,
                )
            if tenant_used + size > self.policy.max_tenant_bytes:
                raise UnslothStorageError(
                    "storage_tenant_total_quota_exceeded",
                    "Tenant storage exceeds its configured total quota.",
                    status_code=413,
                )
            connection.execute(
                """
                INSERT INTO unsloth_storage_artifacts
                    (tenant_id, owner_scope_digest, artifact_id, kind,
                     relative_ref, job_id, attempt_id, artifact_sha256,
                     size_bytes, created_at, retention_until, state,
                     cleanup_task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL)
                """,
                (
                    tenant,
                    owner_scope,
                    identifier,
                    normalized_kind,
                    relative,
                    job,
                    attempt,
                    digest,
                    size,
                    now,
                    record.retention_until,
                ),
            )
            self._bump_revision(connection, tenant)
        return record

    def bind_reference(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        artifact_sha256: str,
        reference_kind: str,
        reference_id: str,
    ) -> None:
        tenant = _tenant(tenant_id)
        artifact = _opaque(artifact_id, "storage_artifact_id_invalid")
        digest = _digest(artifact_sha256, "storage_artifact_hash_invalid")
        kind = str(reference_kind or "").strip().lower()
        if kind not in _REFERENCE_KINDS:
            raise UnslothStorageError(
                "storage_reference_kind_invalid",
                "Storage reference kind is unsupported.",
                status_code=422,
            )
        reference = _opaque(reference_id, "storage_reference_id_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            artifact_row = connection.execute(
                """
                SELECT artifact_sha256
                FROM unsloth_storage_artifacts
                WHERE tenant_id = ? AND artifact_id = ? AND state != 'deleted'
                """,
                (tenant, artifact),
            ).fetchone()
            if artifact_row is None:
                raise UnslothStorageError(
                    "storage_artifact_not_found",
                    "Referenced storage artifact does not exist.",
                    status_code=404,
                )
            if not hmac.compare_digest(str(artifact_row[0]), digest):
                raise UnslothStorageError(
                    "storage_reference_hash_mismatch",
                    "Reference hash differs from immutable artifact identity.",
                )
            existing = connection.execute(
                """
                SELECT artifact_id, artifact_sha256
                FROM unsloth_storage_references
                WHERE tenant_id = ? AND reference_kind = ? AND reference_id = ?
                """,
                (tenant, kind, reference),
            ).fetchone()
            if existing is not None:
                if existing != (artifact, digest):
                    raise UnslothStorageError(
                        "storage_reference_conflict",
                        "Immutable reference is already bound to another artifact.",
                    )
                return
            connection.execute(
                """
                INSERT INTO unsloth_storage_references
                    (tenant_id, reference_kind, reference_id, artifact_id,
                     artifact_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tenant, kind, reference, artifact, digest, float(self._clock())),
            )
            self._bump_revision(connection, tenant)

    def usage(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str | None = None,
    ) -> dict[str, Any]:
        tenant = _tenant(tenant_id)
        owner = (
            _digest(owner_scope_digest, "storage_scope_digest_invalid")
            if owner_scope_digest is not None
            else None
        )
        where = "tenant_id = ? AND state IN ('active', 'cleanup_queued')"
        values: list[Any] = [tenant]
        if owner is not None:
            where += " AND owner_scope_digest = ?"
            values.append(owner)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT kind, COALESCE(SUM(size_bytes), 0), COUNT(*)
                FROM unsloth_storage_artifacts
                WHERE {where}
                GROUP BY kind
                """,
                values,
            ).fetchall()
            revision = self._revision(connection, tenant)
        by_kind = {
            kind: {"bytes": 0, "artifacts": 0}
            for kind in sorted(_ARTIFACT_KINDS)
        }
        for kind, used, count in rows:
            by_kind[str(kind)] = {
                "bytes": int(used),
                "artifacts": int(count),
            }
        return {
            "schema": "ananta.unsloth-storage-usage.v1",
            "catalog_revision": revision,
            "usage": by_kind,
            "tenant_total_bytes": sum(
                int(value["bytes"]) for value in by_kind.values()
            ),
            "quotas": self.policy.public_summary(),
            "paths_exposed": False,
        }

    def list_public(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        tenant = _tenant(tenant_id)
        owner = _digest(owner_scope_digest, "storage_scope_digest_invalid")
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tenant_id, owner_scope_digest, artifact_id, kind,
                       relative_ref, job_id, attempt_id, artifact_sha256,
                       size_bytes, created_at, retention_until, state,
                       cleanup_task_id
                FROM unsloth_storage_artifacts
                WHERE tenant_id = ? AND owner_scope_digest = ?
                  AND state != 'deleted'
                ORDER BY created_at DESC, artifact_id ASC
                LIMIT ?
                """,
                (tenant, owner, bounded_limit),
            ).fetchall()
            result = []
            for row in rows:
                record = _artifact_record(row)
                references = connection.execute(
                    """
                    SELECT reference_kind
                    FROM unsloth_storage_references
                    WHERE tenant_id = ? AND artifact_id = ?
                    ORDER BY reference_kind
                    """,
                    (tenant, record.artifact_id),
                ).fetchall()
                result.append(
                    record.public_summary(
                        reference_kinds=[str(item[0]) for item in references]
                    )
                )
        return tuple(result)

    def plan_cleanup(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        artifact_ids: Sequence[str],
        expected_catalog_revision: int,
        retention_before: float | None = None,
    ) -> CleanupPlan:
        tenant = _tenant(tenant_id)
        owner = _digest(owner_scope_digest, "storage_scope_digest_invalid")
        identifiers = tuple(
            sorted(
                {
                    _opaque(value, "storage_artifact_id_invalid")
                    for value in artifact_ids
                }
            )
        )
        if (
            not identifiers
            or len(identifiers) != len(tuple(artifact_ids))
            or len(identifiers) > self.policy.max_cleanup_items
        ):
            raise UnslothStorageError(
                "storage_cleanup_selection_invalid",
                "Cleanup requires a bounded unique artifact selection.",
                status_code=422,
            )
        if (
            isinstance(expected_catalog_revision, bool)
            or not isinstance(expected_catalog_revision, int)
            or expected_catalog_revision < 0
        ):
            raise UnslothStorageError(
                "storage_catalog_revision_invalid",
                "Cleanup requires a non-negative catalog revision.",
                status_code=422,
            )
        cutoff = (
            float(retention_before)
            if retention_before is not None
            else float(self._clock())
        )
        if not 0 <= cutoff <= 2**63:
            raise UnslothStorageError(
                "storage_retention_cutoff_invalid",
                "Retention cutoff is invalid.",
                status_code=422,
            )
        with self._connect() as connection:
            revision = self._revision(connection, tenant)
            if revision != expected_catalog_revision:
                raise UnslothStorageError(
                    "storage_catalog_revision_conflict",
                    "Storage catalog changed after candidate inspection.",
                )
            placeholders = ",".join("?" for _ in identifiers)
            rows = connection.execute(
                f"""
                SELECT tenant_id, owner_scope_digest, artifact_id, kind,
                       relative_ref, job_id, attempt_id, artifact_sha256,
                       size_bytes, created_at, retention_until, state,
                       cleanup_task_id
                FROM unsloth_storage_artifacts
                WHERE tenant_id = ? AND owner_scope_digest = ?
                  AND artifact_id IN ({placeholders})
                ORDER BY artifact_id
                """,
                (tenant, owner, *identifiers),
            ).fetchall()
            if len(rows) != len(identifiers):
                raise UnslothStorageError(
                    "storage_artifact_not_found",
                    "One or more cleanup artifacts are unavailable in this scope.",
                    status_code=404,
                )
            delegations: list[dict[str, Any]] = []
            protected: list[dict[str, Any]] = []
            now = float(self._clock())
            for row in rows:
                record = _artifact_record(row)
                references = connection.execute(
                    """
                    SELECT reference_kind, reference_id
                    FROM unsloth_storage_references
                    WHERE tenant_id = ? AND artifact_id = ?
                    ORDER BY reference_kind, reference_id
                    """,
                    (tenant, record.artifact_id),
                ).fetchall()
                reason_code: str | None = None
                if record.kind not in {"workspace", "checkpoint", "export"}:
                    reason_code = "storage_cleanup_kind_not_delegable"
                elif record.state != "active":
                    reason_code = "storage_cleanup_state_not_active"
                elif references:
                    reason_code = "storage_artifact_immutably_referenced"
                elif now < record.retention_until or record.created_at > cutoff:
                    reason_code = "storage_retention_not_elapsed"
                if reason_code is not None:
                    protected.append(
                        {
                            "artifact_id": record.artifact_id,
                            "reason_code": reason_code,
                            "references": [
                                {
                                    "kind": str(kind),
                                    "reference_id": str(reference_id),
                                }
                                for kind, reference_id in references
                            ],
                        }
                    )
                    continue
                delegations.append(
                    {
                        "artifact_id": record.artifact_id,
                        "kind": record.kind,
                        "relative_path": record.relative_ref,
                        "job_id": record.job_id,
                        "attempt_id": record.attempt_id,
                        "expected_sha256": record.sha256,
                        "size_bytes": record.size_bytes,
                    }
                )
        plan_payload = {
            "tenant_scope_digest": owner,
            "catalog_revision": revision,
            "artifacts": delegations,
            "protected": protected,
        }
        return CleanupPlan(
            tenant_id=tenant,
            owner_scope_digest=owner,
            catalog_revision=revision,
            artifact_ids=tuple(
                str(item["artifact_id"]) for item in delegations
            ),
            delegations=tuple(delegations),
            protected=tuple(protected),
            total_bytes=sum(int(item["size_bytes"]) for item in delegations),
            plan_sha256=_canonical_sha256(plan_payload),
        )

    def mark_cleanup_queued(
        self,
        *,
        plan: CleanupPlan,
        task_id: str,
    ) -> int:
        task = _opaque(task_id, "storage_cleanup_task_id_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._revision(connection, plan.tenant_id)
            if current != plan.catalog_revision:
                if self._cleanup_plan_is_queued(
                    connection,
                    plan=plan,
                    task_id=task,
                ):
                    return current
                raise UnslothStorageError(
                    "storage_catalog_revision_conflict",
                    "Storage references changed before cleanup was queued.",
                )
            changed = False
            for item in plan.delegations:
                cursor = connection.execute(
                    """
                    UPDATE unsloth_storage_artifacts
                    SET state = 'cleanup_queued', cleanup_task_id = ?
                    WHERE tenant_id = ? AND owner_scope_digest = ?
                      AND artifact_id = ? AND artifact_sha256 = ?
                      AND state = 'active'
                      AND NOT EXISTS (
                        SELECT 1 FROM unsloth_storage_references
                        WHERE tenant_id = ? AND artifact_id = ?
                      )
                    """,
                    (
                        task,
                        plan.tenant_id,
                        plan.owner_scope_digest,
                        item["artifact_id"],
                        item["expected_sha256"],
                        plan.tenant_id,
                        item["artifact_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise UnslothStorageError(
                        "storage_cleanup_reference_conflict",
                        "Artifact became referenced before cleanup admission.",
                    )
                changed = True
            if changed:
                return self._bump_revision(
                    connection,
                    plan.tenant_id,
                )
            return current

    def mark_cleanup_completed(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        task_id: str,
        artifacts: Sequence[Mapping[str, Any]],
    ) -> int:
        """Atomically release quota for one bound Worker cleanup result."""
        tenant = _tenant(tenant_id)
        owner = _digest(
            owner_scope_digest,
            "storage_scope_digest_invalid",
        )
        task = _opaque(task_id, "storage_cleanup_task_id_invalid")
        if (
            not artifacts
            or len(artifacts) > self.policy.max_cleanup_items
        ):
            raise UnslothStorageError(
                "storage_cleanup_result_invalid",
                "Cleanup completion requires a bounded artifact result.",
                status_code=422,
            )
        normalized: dict[str, tuple[str, str]] = {}
        for item in artifacts:
            if not isinstance(item, Mapping):
                raise UnslothStorageError(
                    "storage_cleanup_result_invalid",
                    "Cleanup artifact results must be objects.",
                    status_code=422,
                )
            artifact_id = _opaque(
                str(item.get("artifact_id") or ""),
                "storage_artifact_id_invalid",
            )
            kind = str(item.get("kind") or "").strip().lower()
            sha256 = _digest(
                str(item.get("sha256") or ""),
                "storage_artifact_hash_invalid",
            )
            if (
                kind not in {"workspace", "checkpoint", "export"}
                or artifact_id in normalized
            ):
                raise UnslothStorageError(
                    "storage_cleanup_result_invalid",
                    "Cleanup completion contains an invalid or duplicate artifact.",
                    status_code=422,
                )
            normalized[artifact_id] = (kind, sha256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = False
            for artifact_id, (kind, sha256) in normalized.items():
                row = connection.execute(
                    """
                    SELECT tenant_id, owner_scope_digest, artifact_id, kind,
                           relative_ref, job_id, attempt_id, artifact_sha256,
                           size_bytes, created_at, retention_until, state,
                           cleanup_task_id
                    FROM unsloth_storage_artifacts
                    WHERE tenant_id = ? AND artifact_id = ?
                    """,
                    (tenant, artifact_id),
                ).fetchone()
                if row is None:
                    raise UnslothStorageError(
                        "storage_cleanup_result_binding_invalid",
                        "Cleanup result references an unknown catalog artifact.",
                    )
                record = _artifact_record(row)
                if (
                    record.owner_scope_digest != owner
                    or record.kind != kind
                    or record.sha256 != sha256
                    or record.cleanup_task_id != task
                    or record.state
                    not in {"cleanup_queued", "deleted"}
                ):
                    raise UnslothStorageError(
                        "storage_cleanup_result_binding_invalid",
                        "Cleanup result is not bound to the queued catalog record.",
                    )
                if record.state == "deleted":
                    continue
                reference = connection.execute(
                    """
                    SELECT 1
                    FROM unsloth_storage_references
                    WHERE tenant_id = ? AND artifact_id = ?
                    LIMIT 1
                    """,
                    (tenant, artifact_id),
                ).fetchone()
                if reference is not None:
                    raise UnslothStorageError(
                        "storage_cleanup_reference_conflict",
                        "Artifact became referenced before cleanup completion.",
                    )
                cursor = connection.execute(
                    """
                    UPDATE unsloth_storage_artifacts
                    SET state = 'deleted'
                    WHERE tenant_id = ? AND owner_scope_digest = ?
                      AND artifact_id = ? AND artifact_sha256 = ?
                      AND cleanup_task_id = ? AND state = 'cleanup_queued'
                    """,
                    (tenant, owner, artifact_id, sha256, task),
                )
                if cursor.rowcount != 1:
                    raise UnslothStorageError(
                        "storage_cleanup_result_binding_invalid",
                        "Cleanup catalog completion lost its state fence.",
                    )
                changed = True
            if changed:
                return self._bump_revision(connection, tenant)
            return self._revision(connection, tenant)

    def release_cleanup_queued(
        self,
        *,
        plan: CleanupPlan,
        task_id: str,
    ) -> int:
        """Release a terminally rejected, never-dispatched reservation."""
        task = _opaque(task_id, "storage_cleanup_task_id_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = False
            for item in plan.delegations:
                row = connection.execute(
                    """
                    SELECT owner_scope_digest, kind, artifact_sha256, state,
                           cleanup_task_id
                    FROM unsloth_storage_artifacts
                    WHERE tenant_id = ? AND artifact_id = ?
                    """,
                    (plan.tenant_id, item["artifact_id"]),
                ).fetchone()
                if row is None:
                    raise UnslothStorageError(
                        "storage_cleanup_result_binding_invalid",
                        "Cleanup reservation references an unknown artifact.",
                    )
                binding = (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                )
                expected = (
                    plan.owner_scope_digest,
                    str(item["kind"]),
                    str(item["expected_sha256"]),
                )
                state = str(row[3])
                cleanup_task_id = str(row[4] or "")
                if (
                    state == "active"
                    and not cleanup_task_id
                    and binding == expected
                ):
                    continue
                if (
                    binding != expected
                    or state != "cleanup_queued"
                    or cleanup_task_id != task
                ):
                    raise UnslothStorageError(
                        "storage_cleanup_result_binding_invalid",
                        "Cleanup reservation cannot release a foreign state.",
                    )
                cursor = connection.execute(
                    """
                    UPDATE unsloth_storage_artifacts
                    SET state = 'active', cleanup_task_id = NULL
                    WHERE tenant_id = ? AND owner_scope_digest = ?
                      AND artifact_id = ? AND artifact_sha256 = ?
                      AND state = 'cleanup_queued'
                      AND cleanup_task_id = ?
                    """,
                    (
                        plan.tenant_id,
                        plan.owner_scope_digest,
                        item["artifact_id"],
                        item["expected_sha256"],
                        task,
                    ),
                )
                if cursor.rowcount != 1:
                    raise UnslothStorageError(
                        "storage_cleanup_result_binding_invalid",
                        "Cleanup reservation release lost its state fence.",
                    )
                changed = True
            if changed:
                return self._bump_revision(
                    connection,
                    plan.tenant_id,
                )
            return self._revision(connection, plan.tenant_id)

    @staticmethod
    def _cleanup_plan_is_queued(
        connection: sqlite3.Connection,
        *,
        plan: CleanupPlan,
        task_id: str,
    ) -> bool:
        if not plan.delegations:
            return False
        for item in plan.delegations:
            row = connection.execute(
                """
                SELECT owner_scope_digest, kind, artifact_sha256, state,
                       cleanup_task_id
                FROM unsloth_storage_artifacts
                WHERE tenant_id = ? AND artifact_id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM unsloth_storage_references
                    WHERE tenant_id = ? AND artifact_id = ?
                  )
                """,
                (
                    plan.tenant_id,
                    item["artifact_id"],
                    plan.tenant_id,
                    item["artifact_id"],
                ),
            ).fetchone()
            if (
                row is None
                or str(row[0]) != plan.owner_scope_digest
                or str(row[1]) != item["kind"]
                or str(row[2]) != item["expected_sha256"]
                or str(row[3])
                not in {"cleanup_queued", "deleted"}
                or str(row[4] or "") != task_id
            ):
                return False
        return True

    def _initialize(self) -> None:
        with self._initialization_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._connect() as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.executescript(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _revision(connection: sqlite3.Connection, tenant_id: str) -> int:
        row = connection.execute(
            """
            SELECT revision FROM unsloth_storage_revisions
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _bump_revision(
        connection: sqlite3.Connection,
        tenant_id: str,
    ) -> int:
        connection.execute(
            """
            INSERT INTO unsloth_storage_revisions (tenant_id, revision)
            VALUES (?, 1)
            ON CONFLICT(tenant_id) DO UPDATE SET revision = revision + 1
            """,
            (tenant_id,),
        )
        return SqliteUnslothStorageCatalog._revision(connection, tenant_id)


class UnslothStorageCleanupMutationExecutor:
    """Revalidates a cleanup plan and queues it through the central Hub."""

    def __init__(
        self,
        *,
        catalog: SqliteUnslothStorageCatalog,
        tasks: HubTaskSubmissionPort,
    ) -> None:
        self._catalog = catalog
        self._tasks = tasks

    def preview(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
    ) -> Mapping[str, Any]:
        del principal, resource_id, reason
        raise UnslothMutationError(
            "unsloth_cleanup_contract_required",
            "Cleanup requires explicit artifact IDs and a catalog revision.",
        )

    def execute(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del principal, resource_id, reason, idempotency_key
        raise UnslothMutationError(
            "unsloth_cleanup_contract_required",
            "Cleanup requires explicit artifact IDs and a catalog revision.",
        )

    def preview_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        operation_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del resource_id, reason
        return self._plan(principal, operation_payload).public_summary()

    def execute_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
        operation_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key or len(normalized_key) > 256:
            raise UnslothMutationError(
                "unsloth_task_idempotency_key_invalid",
                "Cleanup requires a bounded idempotency key.",
                status_code=422,
            )
        task_id = derive_unsloth_task_id(
            tenant_id=principal.tenant_id,
            task_type="ml.storage.cleanup",
            idempotency_key=normalized_key,
        )
        existing = self._tasks.get_submission(task_id)
        if existing is not None:
            return self._reconcile_submission(
                principal=principal,
                reason=reason,
                operation_payload=operation_payload,
                task_id=task_id,
                submission=existing,
            )
        plan = self._plan(principal, operation_payload)
        if not plan.delegations:
            raise UnslothMutationError(
                "unsloth_cleanup_no_deletable_artifacts",
                "No selected artifact is retention-eligible and unreferenced.",
                status_code=409,
            )
        del resource_id
        payload = {
            "contract_version": "ananta.unsloth-storage-cleanup-task.v1",
            "task_id": task_id,
            "tenant_scope_digest": plan.owner_scope_digest,
            "catalog_revision": plan.catalog_revision,
            "plan_sha256": plan.plan_sha256,
            "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            "artifacts": [
                {
                    "artifact_id": item["artifact_id"],
                    "kind": item["kind"],
                    "relative_ref": item["relative_path"],
                    "job_id": item["job_id"],
                    "attempt_id": item["attempt_id"],
                    "sha256": item["expected_sha256"],
                    "size_bytes": item["size_bytes"],
                }
                for item in plan.delegations
            ],
        }
        try:
            reserved_task_id = self._tasks.reserve(
                task_type="ml.storage.cleanup",
                tenant_id=principal.tenant_id,
                payload=payload,
                idempotency_key=normalized_key,
            )
            if reserved_task_id != task_id:
                raise ValueError("unsloth_cleanup_task_identity_mismatch")
        except ValueError as exc:
            raise UnslothMutationError(
                str(exc),
                "Hub cleanup task reservation failed.",
                status_code=409,
            ) from exc
        try:
            catalog_revision = self._catalog.mark_cleanup_queued(
                plan=plan,
                task_id=task_id,
            )
        except UnslothStorageError as exc:
            self._tasks.reject_reserved(
                task_id,
                reason_code=exc.reason_code,
            )
            raise _mutation_error(exc) from exc
        if not self._tasks.activate_reserved(task_id):
            raise UnslothMutationError(
                "unsloth_cleanup_task_activation_pending",
                "Cleanup reservation is fenced but not yet dispatchable.",
                status_code=503,
            )
        return {
            **plan.public_summary(),
            "task_id": task_id,
            "status": "queued",
            "catalog_revision_after": catalog_revision,
            "reason_code": "unsloth_cleanup_queued",
        }

    def _reconcile_submission(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        reason: str,
        operation_payload: Mapping[str, Any],
        task_id: str,
        submission: Mapping[str, object],
    ) -> Mapping[str, Any]:
        payload = submission.get("payload")
        try:
            raw_requested_ids = tuple(
                _opaque(value, "storage_artifact_id_invalid")
                for value in tuple(
                    operation_payload.get("artifact_ids") or ()
                )
            )
            requested_ids = set(raw_requested_ids)
            expected_revision = int(
                operation_payload.get("expected_catalog_revision")
            )
        except (
            TypeError,
            ValueError,
            UnslothStorageError,
        ) as exc:
            raise UnslothMutationError(
                "storage_cleanup_contract_invalid",
                "Cleanup replay contains invalid selection fields.",
                status_code=422,
            ) from exc
        if (
            submission.get("task_id") != task_id
            or submission.get("task_type") != "ml.storage.cleanup"
            or submission.get("tenant_id") != principal.tenant_id
            or submission.get("result_handler")
            != "unsloth_storage_cleanup_v1"
            or not isinstance(payload, Mapping)
            or not requested_ids
            or len(requested_ids) != len(raw_requested_ids)
        ):
            raise UnslothMutationError(
                "unsloth_task_idempotency_conflict",
                "Cleanup task identity is bound to another submission.",
                status_code=409,
            )
        plan = self._plan_from_submission(
            tenant_id=principal.tenant_id,
            task_id=task_id,
            payload=payload,
        )
        reason_sha256 = hashlib.sha256(
            reason.encode("utf-8")
        ).hexdigest()
        if (
            plan.catalog_revision != expected_revision
            or plan.owner_scope_digest
            != tenant_scope_digest(principal)
            or payload.get("reason_sha256") != reason_sha256
            or not set(plan.artifact_ids).issubset(requested_ids)
        ):
            raise UnslothMutationError(
                "unsloth_task_idempotency_conflict",
                "Cleanup idempotency key is bound to another request.",
                status_code=409,
            )
        status = str(submission.get("status") or "")
        if status == "cancelled":
            raise UnslothMutationError(
                "unsloth_cleanup_admission_rejected",
                "The reserved cleanup admission was rejected.",
                status_code=409,
            )
        if status in {"completed", "failed"}:
            return {
                **plan.public_summary(),
                "task_id": task_id,
                "status": status,
                "catalog_revision_after": self._catalog.usage(
                    tenant_id=principal.tenant_id,
                    owner_scope_digest=plan.owner_scope_digest,
                )["catalog_revision"],
                "reason_code": (
                    "unsloth_cleanup_completed"
                    if status == "completed"
                    else "unsloth_cleanup_failed"
                ),
                "replayed": True,
            }
        if status not in {
            "reserved",
            "todo",
            "created",
            "assigned",
            "in_progress",
            "delegated",
        }:
            raise UnslothMutationError(
                "unsloth_cleanup_task_state_invalid",
                "Cleanup reservation has an incompatible task state.",
                status_code=409,
            )
        try:
            catalog_revision = self._catalog.mark_cleanup_queued(
                plan=plan,
                task_id=task_id,
            )
        except UnslothStorageError as exc:
            if status == "reserved":
                self._tasks.reject_reserved(
                    task_id,
                    reason_code=exc.reason_code,
                )
            raise _mutation_error(exc) from exc
        if status == "reserved" and not self._tasks.activate_reserved(
            task_id
        ):
            raise UnslothMutationError(
                "unsloth_cleanup_task_activation_pending",
                "Cleanup reservation remains fenced for reconciliation.",
                status_code=503,
            )
        return {
            **plan.public_summary(),
            "task_id": task_id,
            "status": "queued",
            "catalog_revision_after": catalog_revision,
            "reason_code": "unsloth_cleanup_queued",
            "replayed": True,
        }

    @staticmethod
    def _plan_from_submission(
        *,
        tenant_id: str,
        task_id: str,
        payload: Mapping[str, object],
    ) -> CleanupPlan:
        artifacts = payload.get("artifacts")
        if (
            set(payload)
            != {
                "contract_version",
                "task_id",
                "tenant_scope_digest",
                "catalog_revision",
                "plan_sha256",
                "reason_sha256",
                "artifacts",
            }
            or payload.get("contract_version")
            != "ananta.unsloth-storage-cleanup-task.v1"
            or payload.get("task_id") != task_id
            or not isinstance(artifacts, list)
            or not 1 <= len(artifacts) <= 128
            or isinstance(payload.get("catalog_revision"), bool)
            or not isinstance(payload.get("catalog_revision"), int)
            or int(payload["catalog_revision"]) < 0
        ):
            raise UnslothMutationError(
                "unsloth_cleanup_reservation_invalid",
                "Stored cleanup reservation is malformed.",
                status_code=409,
            )
        delegations: list[dict[str, Any]] = []
        try:
            for item in artifacts:
                if (
                    not isinstance(item, Mapping)
                    or set(item)
                    != {
                        "artifact_id",
                        "kind",
                        "relative_ref",
                        "job_id",
                        "attempt_id",
                        "sha256",
                        "size_bytes",
                    }
                    or item.get("kind")
                    not in {"workspace", "checkpoint", "export"}
                    or isinstance(item.get("size_bytes"), bool)
                    or not isinstance(item.get("size_bytes"), int)
                    or int(item["size_bytes"]) < 0
                ):
                    raise ValueError
                artifact_id = _opaque(
                    str(item["artifact_id"]),
                    "storage_artifact_id_invalid",
                )
                job_id = _opaque(
                    str(item["job_id"]),
                    "storage_job_id_invalid",
                )
                attempt_id = _opaque(
                    str(item["attempt_id"]),
                    "storage_attempt_id_invalid",
                )
                artifact_sha256 = _digest(
                    str(item["sha256"]),
                    "storage_artifact_hash_invalid",
                )
                relative_ref = _scoped_relative_ref(
                    tenant_id=tenant_id,
                    owner_scope_digest=str(
                        payload["tenant_scope_digest"]
                    ),
                    kind=str(item["kind"]),
                    relative_ref=str(item["relative_ref"]),
                    job_id=job_id,
                    attempt_id=attempt_id,
                )
                delegations.append(
                    {
                        "artifact_id": artifact_id,
                        "kind": str(item["kind"]),
                        "relative_path": relative_ref,
                        "job_id": job_id,
                        "attempt_id": attempt_id,
                        "expected_sha256": artifact_sha256,
                        "size_bytes": int(item["size_bytes"]),
                    }
                )
            if len(
                {
                    str(item["artifact_id"])
                    for item in delegations
                }
            ) != len(delegations):
                raise ValueError
            return CleanupPlan(
                tenant_id=_tenant(tenant_id),
                owner_scope_digest=_digest(
                    str(payload["tenant_scope_digest"]),
                    "storage_scope_digest_invalid",
                ),
                catalog_revision=int(payload["catalog_revision"]),
                artifact_ids=tuple(
                    str(item["artifact_id"])
                    for item in delegations
                ),
                delegations=tuple(delegations),
                protected=(),
                total_bytes=sum(
                    int(item["size_bytes"])
                    for item in delegations
                ),
                plan_sha256=_digest(
                    str(payload["plan_sha256"]),
                    "storage_cleanup_plan_hash_invalid",
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            UnslothStorageError,
        ) as exc:
            raise UnslothMutationError(
                "unsloth_cleanup_reservation_invalid",
                "Stored cleanup reservation is malformed.",
                status_code=409,
            ) from exc

    def _plan(
        self,
        principal: MlInternTrainingPrincipal,
        operation_payload: Mapping[str, Any],
    ) -> CleanupPlan:
        try:
            return self._catalog.plan_cleanup(
                tenant_id=principal.tenant_id,
                owner_scope_digest=tenant_scope_digest(principal),
                artifact_ids=tuple(operation_payload.get("artifact_ids") or ()),
                expected_catalog_revision=int(
                    operation_payload.get("expected_catalog_revision")
                ),
                retention_before=operation_payload.get("retention_before"),
            )
        except (TypeError, ValueError) as exc:
            raise UnslothMutationError(
                "storage_cleanup_contract_invalid",
                "Cleanup contract contains invalid numeric values.",
            ) from exc
        except UnslothStorageError as exc:
            raise _mutation_error(exc) from exc


def cleanup_plan_from_submission(
    *,
    tenant_id: str,
    task_id: str,
    payload: Mapping[str, object],
) -> CleanupPlan:
    """Rebuild a path-contained admission plan from a durable Hub task."""
    return UnslothStorageCleanupMutationExecutor._plan_from_submission(
        tenant_id=tenant_id,
        task_id=task_id,
        payload=payload,
    )


def tenant_scope_digest(principal: MlInternTrainingPrincipal) -> str:
    material = (
        "ananta.ml-intern-training.scope.v1\x00"
        f"{principal.tenant_id}\x00{principal.subject}"
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def storage_catalog_from_config(
    config: Mapping[str, Any],
) -> SqliteUnslothStorageCatalog:
    training = (
        dict(config.get("ml_intern_training") or {})
        if isinstance(config.get("ml_intern_training"), Mapping)
        else dict(config)
    )
    artifact_root = Path(
        str(training.get("artifact_root") or "artifacts/lora")
    )
    individual = {
        "max_dataset_bytes": int(
            training.get("max_dataset_bytes") or 4 * 1024**3
        ),
        "max_model_bytes": int(
            training.get("max_model_bytes") or 20 * 1024**3
        ),
        "max_checkpoint_bytes": int(
            training.get("max_checkpoint_bytes") or 8 * 1024**3
        ),
        "max_export_bytes": int(
            training.get("max_export_bytes") or 20 * 1024**3
        ),
    }
    maximum = max(individual.values())
    policy = UnslothStorageQuotaPolicy(
        **individual,
        max_tenant_bytes=max(
            maximum,
            int(training.get("max_tenant_storage_bytes") or 64 * 1024**3),
        ),
        retention_seconds=int(
            training.get("storage_retention_seconds") or 30 * 24 * 60 * 60
        ),
        max_cleanup_items=int(training.get("max_cleanup_items") or 128),
    )
    path = (
        artifact_root
        / ".control"
        / "unsloth-storage-governance.sqlite3"
    )
    return SqliteUnslothStorageCatalog(path, policy=policy)


def _artifact_record(row: Sequence[Any]) -> StorageArtifactRecord:
    return StorageArtifactRecord(
        tenant_id=str(row[0]),
        owner_scope_digest=str(row[1]),
        artifact_id=str(row[2]),
        kind=str(row[3]),
        relative_ref=str(row[4]),
        job_id=str(row[5]),
        attempt_id=str(row[6]),
        sha256=str(row[7]),
        size_bytes=int(row[8]),
        created_at=float(row[9]),
        retention_until=float(row[10]),
        state=str(row[11]),
        cleanup_task_id=str(row[12]) if row[12] is not None else None,
    )


def _scoped_relative_ref(
    *,
    tenant_id: str,
    owner_scope_digest: str,
    kind: str,
    relative_ref: str,
    job_id: str,
    attempt_id: str,
) -> str:
    raw = str(relative_ref or "")
    pure = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or "\\" in raw
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise UnslothStorageError(
            "storage_relative_ref_invalid",
            "Storage reference must be a contained relative path.",
            status_code=422,
        )
    parts = pure.parts
    if kind == "dataset":
        tenant_key = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        expected = ("tenants", tenant_key, "datasets")
        valid = len(parts) > len(expected) and parts[: len(expected)] == expected
    else:
        expected = (
            "tenants",
            owner_scope_digest,
            "jobs",
            job_id,
            "attempts",
            attempt_id,
        )
        suffix = parts[len(expected) :]
        valid = (
            len(parts) > len(expected)
            and parts[: len(expected)] == expected
            and (
                (kind == "workspace" and suffix[0] == "workspace")
                or (kind == "model" and suffix[0] == "model-cache")
                or (kind == "checkpoint" and suffix[0] == "checkpoints")
                or (
                    kind == "export"
                    and suffix[0] in {"adapter", "artifacts", "exports"}
                )
            )
        )
    if not valid:
        raise UnslothStorageError(
            "storage_scope_binding_mismatch",
            "Storage path is not bound to its tenant, job, attempt, and kind.",
        )
    return pure.as_posix()


def _tenant(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise UnslothStorageError(
            "storage_tenant_invalid",
            "A bounded tenant ID is required.",
            status_code=422,
        )
    return normalized


def _opaque(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_ID.fullmatch(normalized) is None:
        raise UnslothStorageError(
            reason_code,
            "An opaque identifier is required.",
            status_code=422,
        )
    return normalized


def _digest(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256.fullmatch(normalized) is None:
        raise UnslothStorageError(
            reason_code,
            "A lowercase SHA-256 digest is required.",
            status_code=422,
        )
    return normalized


def _bounded_size(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 2**63 - 1
    ):
        raise UnslothStorageError(
            "storage_size_invalid",
            "Storage size must be a bounded non-negative integer.",
            status_code=422,
        )
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _mutation_error(exc: UnslothStorageError) -> UnslothMutationError:
    return UnslothMutationError(
        exc.reason_code,
        str(exc),
        status_code=exc.status_code,
        retryable=exc.retryable,
    )


__all__ = [
    "CleanupPlan",
    "SqliteUnslothStorageCatalog",
    "StorageArtifactRecord",
    "StorageReferencePort",
    "UnslothStorageCleanupMutationExecutor",
    "UnslothStorageError",
    "UnslothStorageQuotaPolicy",
    "storage_catalog_from_config",
    "tenant_scope_digest",
]
