"""Cleanup planning and fenced catalog mutations for Unsloth storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.unsloth_storage_contracts import CleanupPlan, UnslothStorageError
from agent.services.unsloth_storage_validation import (
    artifact_record,
    canonical_sha256,
)
from agent.services.unsloth_storage_validation import (
    digest as validate_digest,
)
from agent.services.unsloth_storage_validation import (
    opaque as validate_opaque,
)
from agent.services.unsloth_storage_validation import (
    tenant as validate_tenant,
)


class UnslothStorageCatalogCleanupMixin:
    def plan_cleanup(
        self,
        *,
        tenant_id: str,
        owner_scope_digest: str,
        artifact_ids: Sequence[str],
        expected_catalog_revision: int,
        retention_before: float | None = None,
    ) -> CleanupPlan:
        tenant = validate_tenant(tenant_id)
        owner = validate_digest(owner_scope_digest, "storage_scope_digest_invalid")
        identifiers = tuple(sorted({validate_opaque(value, "storage_artifact_id_invalid") for value in artifact_ids}))
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
        cutoff = float(retention_before) if retention_before is not None else float(self._clock())
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
                record = artifact_record(row)
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
            artifact_ids=tuple(str(item["artifact_id"]) for item in delegations),
            delegations=tuple(delegations),
            protected=tuple(protected),
            total_bytes=sum(int(item["size_bytes"]) for item in delegations),
            plan_sha256=canonical_sha256(plan_payload),
        )

    def mark_cleanup_queued(
        self,
        *,
        plan: CleanupPlan,
        task_id: str,
    ) -> int:
        task = validate_opaque(task_id, "storage_cleanup_task_id_invalid")
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
        tenant = validate_tenant(tenant_id)
        owner = validate_digest(
            owner_scope_digest,
            "storage_scope_digest_invalid",
        )
        task = validate_opaque(task_id, "storage_cleanup_task_id_invalid")
        if not artifacts or len(artifacts) > self.policy.max_cleanup_items:
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
            artifact_id = validate_opaque(
                str(item.get("artifact_id") or ""),
                "storage_artifact_id_invalid",
            )
            kind = str(item.get("kind") or "").strip().lower()
            sha256 = validate_digest(
                str(item.get("sha256") or ""),
                "storage_artifact_hash_invalid",
            )
            if kind not in {"workspace", "checkpoint", "export"} or artifact_id in normalized:
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
                record = artifact_record(row)
                if (
                    record.owner_scope_digest != owner
                    or record.kind != kind
                    or record.sha256 != sha256
                    or record.cleanup_task_id != task
                    or record.state not in {"cleanup_queued", "deleted"}
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
        task = validate_opaque(task_id, "storage_cleanup_task_id_invalid")
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
                if state == "active" and not cleanup_task_id and binding == expected:
                    continue
                if binding != expected or state != "cleanup_queued" or cleanup_task_id != task:
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
                or str(row[3]) not in {"cleanup_queued", "deleted"}
                or str(row[4] or "") != task_id
            ):
                return False
        return True
