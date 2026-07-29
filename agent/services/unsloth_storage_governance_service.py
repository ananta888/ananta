"""Hub-owned storage quotas, references, retention, and cleanup admission."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_mutation_command_service import (
    UnslothMutationError,
)
from agent.services.unsloth_storage_catalog_cleanup_mixin import (
    UnslothStorageCatalogCleanupMixin,
)
from agent.services.unsloth_storage_catalog_registration_mixin import (
    UnslothStorageCatalogRegistrationMixin,
)
from agent.services.unsloth_storage_contracts import (
    CleanupPlan,
    StorageArtifactRecord,
    StorageCleanupAdmissionPort,
    StorageCleanupCompletionPort,
    StorageReferencePort,
    UnslothStorageCleanupCatalogPort,
    UnslothStorageError,
    UnslothStorageQuotaPolicy,
)
from agent.services.unsloth_storage_validation import (
    digest as _digest,
)
from agent.services.unsloth_storage_validation import (
    opaque as _opaque,
)
from agent.services.unsloth_storage_validation import (
    scoped_relative_ref as _scoped_relative_ref,
)
from agent.services.unsloth_storage_validation import (
    tenant as _tenant,
)
from agent.services.unsloth_task_port import (
    HubTaskSubmissionPort,
    derive_unsloth_task_id,
)


class SqliteUnslothStorageCatalog(
    UnslothStorageCatalogRegistrationMixin,
    UnslothStorageCatalogCleanupMixin,
    StorageReferencePort,
):
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
        catalog: UnslothStorageCleanupCatalogPort,
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
    "StorageCleanupAdmissionPort",
    "StorageCleanupCompletionPort",
    "StorageReferencePort",
    "UnslothStorageCleanupMutationExecutor",
    "UnslothStorageError",
    "UnslothStorageQuotaPolicy",
    "storage_catalog_from_config",
    "tenant_scope_digest",
]
