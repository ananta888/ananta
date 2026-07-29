"""SQLite catalog registration and read projections.

The mixin depends only on the catalog connection/revision seam supplied by the
public governance facade.
"""

from __future__ import annotations

import hmac
from typing import Any

from agent.services.unsloth_storage_contracts import (
    ARTIFACT_KINDS,
    REFERENCE_KINDS,
    StorageArtifactRecord,
    UnslothStorageError,
)
from agent.services.unsloth_storage_validation import (
    artifact_record,
)
from agent.services.unsloth_storage_validation import (
    bounded_size as validate_size,
)
from agent.services.unsloth_storage_validation import (
    digest as validate_digest,
)
from agent.services.unsloth_storage_validation import (
    opaque as validate_opaque,
)
from agent.services.unsloth_storage_validation import (
    scoped_relative_ref as validate_relative_ref,
)
from agent.services.unsloth_storage_validation import (
    tenant as validate_tenant,
)


class UnslothStorageCatalogRegistrationMixin:
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
        tenant = validate_tenant(tenant_id)
        owner_scope = validate_digest(owner_scope_digest, "storage_scope_digest_invalid")
        identifier = validate_opaque(artifact_id, "storage_artifact_id_invalid")
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in ARTIFACT_KINDS:
            raise UnslothStorageError(
                "storage_artifact_kind_invalid",
                "Storage artifact kind is unsupported.",
                status_code=422,
            )
        job = validate_opaque(job_id, "storage_job_id_invalid")
        attempt = validate_opaque(attempt_id, "storage_attempt_id_invalid")
        digest = validate_digest(artifact_sha256, "storage_artifact_hash_invalid")
        size = validate_size(size_bytes)
        relative = validate_relative_ref(
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
                current = artifact_record(existing)
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
        tenant = validate_tenant(tenant_id)
        artifact = validate_opaque(artifact_id, "storage_artifact_id_invalid")
        digest = validate_digest(artifact_sha256, "storage_artifact_hash_invalid")
        kind = str(reference_kind or "").strip().lower()
        if kind not in REFERENCE_KINDS:
            raise UnslothStorageError(
                "storage_reference_kind_invalid",
                "Storage reference kind is unsupported.",
                status_code=422,
            )
        reference = validate_opaque(reference_id, "storage_reference_id_invalid")
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
        tenant = validate_tenant(tenant_id)
        owner = (
            validate_digest(owner_scope_digest, "storage_scope_digest_invalid")
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
        by_kind = {kind: {"bytes": 0, "artifacts": 0} for kind in sorted(ARTIFACT_KINDS)}
        for kind, used, count in rows:
            by_kind[str(kind)] = {
                "bytes": int(used),
                "artifacts": int(count),
            }
        return {
            "schema": "ananta.unsloth-storage-usage.v1",
            "catalog_revision": revision,
            "usage": by_kind,
            "tenant_total_bytes": sum(int(value["bytes"]) for value in by_kind.values()),
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
        tenant = validate_tenant(tenant_id)
        owner = validate_digest(owner_scope_digest, "storage_scope_digest_invalid")
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
                record = artifact_record(row)
                references = connection.execute(
                    """
                    SELECT reference_kind
                    FROM unsloth_storage_references
                    WHERE tenant_id = ? AND artifact_id = ?
                    ORDER BY reference_kind
                    """,
                    (tenant, record.artifact_id),
                ).fetchall()
                result.append(record.public_summary(reference_kinds=[str(item[0]) for item in references]))
        return tuple(result)
