"""Append-only Hub metadata catalog for immutable imported model versions."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from flask import current_app, has_app_context

from ananta_contracts.model_catalog import ImportedModelVersion
from ananta_contracts.unsloth_task import (
    canonical_unsloth_json,
    unsloth_payload_sha256,
)
from agent.services.ml_intern_provenance_contract import normalize_source_ids


class UnslothModelCatalogError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class ModelImportCompletionOutboxEntry:
    outbox_id: str
    task_id: str
    catalog_revision: int
    response_sha256: str
    projection: dict[str, Any]
    state: str
    publishes_catalog: bool
    attempts: int
    last_error: str | None


class ImportedModelCatalogPort(Protocol):
    def promote(self, metadata: Mapping[str, Any]) -> ImportedModelVersion: ...

    def list_versions(self, *, tenant_id: str) -> tuple[ImportedModelVersion, ...]: ...

    def promote_with_completion_outbox(
        self,
        metadata: Mapping[str, Any],
        *,
        task_id: str,
        worker_envelope: Mapping[str, Any],
    ) -> tuple[ImportedModelVersion, ModelImportCompletionOutboxEntry]: ...


class SqliteUnslothModelCatalogRegistry:
    """Stores immutable metadata rows; model bytes always remain worker-owned."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS unsloth_imported_model_versions (
            catalog_revision INTEGER PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL,
            UNIQUE (tenant_id, model_id, version),
            UNIQUE (tenant_id, model_id, content_sha256)
        )
    """
    _OUTBOX_SCHEMA = """
        CREATE TABLE IF NOT EXISTS unsloth_model_import_completion_outbox (
            outbox_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            catalog_revision INTEGER NOT NULL,
            response_sha256 TEXT NOT NULL,
            projection_json TEXT NOT NULL,
            state TEXT NOT NULL,
            publishes_catalog INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (catalog_revision)
                REFERENCES unsloth_imported_model_versions(catalog_revision)
        )
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.execute(self._SCHEMA)
            connection.execute(self._OUTBOX_SCHEMA)

    def promote(self, metadata: Mapping[str, Any]) -> ImportedModelVersion:
        normalized = _normalize_metadata(metadata)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record, _created = self._promote_in_transaction(
                connection,
                normalized,
            )
            return record

    def promote_with_completion_outbox(
        self,
        metadata: Mapping[str, Any],
        *,
        task_id: str,
        worker_envelope: Mapping[str, Any],
    ) -> tuple[
        ImportedModelVersion,
        ModelImportCompletionOutboxEntry,
    ]:
        normalized = _normalize_metadata(metadata)
        normalized_task_id = str(task_id or "").strip()
        envelope = dict(worker_envelope)
        if (
            not normalized_task_id
            or envelope.get("task_id") != normalized_task_id
            or envelope.get("task_type") != "ml.model.import"
            or envelope.get("status") != "completed"
            or envelope.get("reason_code") is not None
            or not isinstance(envelope.get("result"), Mapping)
        ):
            raise UnslothModelCatalogError(
                "model_import_completion_outbox_invalid",
                "The completion outbox requires one validated completed Worker envelope.",
            )
        response_sha256 = unsloth_payload_sha256(envelope)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record, created = self._promote_in_transaction(
                connection,
                normalized,
            )
            projection = {
                "unsloth_worker_result": envelope,
                "unsloth_model_import": record.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            }
            projection_json = canonical_unsloth_json(
                projection
            )
            existing = connection.execute(
                """
                SELECT outbox_id, task_id, catalog_revision,
                       response_sha256, projection_json, state,
                       publishes_catalog, attempts, last_error
                FROM unsloth_model_import_completion_outbox
                WHERE task_id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if existing is not None:
                entry = self._outbox_entry(existing)
                if (
                    entry.catalog_revision
                    != record.catalog_revision
                    or entry.response_sha256
                    != response_sha256
                    or canonical_unsloth_json(
                        entry.projection
                    )
                    != projection_json
                ):
                    raise UnslothModelCatalogError(
                        "model_import_completion_replay_conflict",
                        "A different completion is already bound to this task.",
                    )
                return record, entry
            outbox_id = "umio-" + hashlib.sha256(
                (
                    f"{normalized_task_id}\0"
                    f"{record.catalog_revision}\0"
                    f"{response_sha256}"
                ).encode("utf-8")
            ).hexdigest()[:32]
            connection.execute(
                """
                INSERT INTO unsloth_model_import_completion_outbox (
                    outbox_id, task_id, catalog_revision,
                    response_sha256, projection_json, state,
                    publishes_catalog, attempts, last_error,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, NULL, ?, ?)
                """,
                (
                    outbox_id,
                    normalized_task_id,
                    record.catalog_revision,
                    response_sha256,
                    projection_json,
                    1 if created else 0,
                    now,
                    now,
                ),
            )
            return record, ModelImportCompletionOutboxEntry(
                outbox_id=outbox_id,
                task_id=normalized_task_id,
                catalog_revision=record.catalog_revision,
                response_sha256=response_sha256,
                projection=projection,
                state="pending",
                publishes_catalog=created,
                attempts=0,
                last_error=None,
            )

    @staticmethod
    def _promote_in_transaction(
        connection: sqlite3.Connection,
        normalized: Mapping[str, Any],
    ) -> tuple[ImportedModelVersion, bool]:
        existing = connection.execute(
            """
            SELECT record_json FROM unsloth_imported_model_versions
            WHERE tenant_id = ? AND model_id = ? AND content_sha256 = ?
            """,
            (
                normalized["tenant_id"],
                normalized["model_id"],
                normalized["content_sha256"],
            ),
        ).fetchone()
        if existing is not None:
            return (
                ImportedModelVersion.model_validate(
                    json.loads(existing[0])
                ),
                False,
            )
        row = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0)
            FROM unsloth_imported_model_versions
            WHERE tenant_id = ? AND model_id = ?
            """,
            (
                normalized["tenant_id"],
                normalized["model_id"],
            ),
        ).fetchone()
        version = int(row[0] or 0) + 1
        revision_row = connection.execute(
            """
            SELECT COALESCE(MAX(catalog_revision), 0)
            FROM unsloth_imported_model_versions
            """
        ).fetchone()
        catalog_revision = int(revision_row[0] or 0) + 1
        version_id = "imv-" + hashlib.sha256(
            (
                f"{normalized['tenant_id']}\0"
                f"{normalized['model_id']}\0"
                f"{version}\0"
                f"{normalized['content_sha256']}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        record = ImportedModelVersion.model_validate(
            {
                "version_id": version_id,
                "catalog_revision": catalog_revision,
                "version": version,
                **dict(normalized),
            }
        )
        encoded = canonical_unsloth_json(
            record.model_dump(mode="json", by_alias=True)
        )
        connection.execute(
            """
            INSERT INTO unsloth_imported_model_versions (
                catalog_revision, tenant_id, model_id,
                version, content_sha256, record_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                catalog_revision,
                record.tenant_id,
                record.model_id,
                record.version,
                record.content_sha256,
                encoded,
            ),
        )
        return record, True

    def list_versions(self, *, tenant_id: str) -> tuple[ImportedModelVersion, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT model.record_json
                FROM unsloth_imported_model_versions AS model
                WHERE model.tenant_id = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM unsloth_model_import_completion_outbox AS outbox
                    WHERE outbox.catalog_revision = model.catalog_revision
                      AND outbox.publishes_catalog = 1
                      AND outbox.state != 'terminalized'
                  )
                ORDER BY model.catalog_revision ASC
                """,
                (tenant_id,),
            ).fetchall()
        return tuple(ImportedModelVersion.model_validate(json.loads(row[0])) for row in rows)

    def get_completion_outbox(
        self,
        *,
        task_id: str,
    ) -> ModelImportCompletionOutboxEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT outbox_id, task_id, catalog_revision,
                       response_sha256, projection_json, state,
                       publishes_catalog, attempts, last_error
                FROM unsloth_model_import_completion_outbox
                WHERE task_id = ?
                """,
                (str(task_id or ""),),
            ).fetchone()
        return (
            self._outbox_entry(row)
            if row is not None
            else None
        )

    def list_pending_completion_outbox(
        self,
        *,
        limit: int = 100,
    ) -> tuple[ModelImportCompletionOutboxEntry, ...]:
        bounded_limit = max(1, min(int(limit), 1000))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT outbox_id, task_id, catalog_revision,
                       response_sha256, projection_json, state,
                       publishes_catalog, attempts, last_error
                FROM unsloth_model_import_completion_outbox
                WHERE state = 'pending'
                ORDER BY created_at ASC, outbox_id ASC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return tuple(
            self._outbox_entry(row)
            for row in rows
        )

    def mark_completion_outbox_terminalized(
        self,
        *,
        outbox_id: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                """
                UPDATE unsloth_model_import_completion_outbox
                SET state = 'terminalized',
                    attempts = attempts + 1,
                    last_error = NULL,
                    updated_at = ?
                WHERE outbox_id = ?
                  AND state IN ('pending', 'terminalized')
                """,
                (time.time(), str(outbox_id or "")),
            )
            if int(result.rowcount or 0) != 1:
                raise UnslothModelCatalogError(
                    "model_import_completion_outbox_missing",
                    "The completion outbox entry is unavailable.",
                )

    def record_completion_outbox_failure(
        self,
        *,
        outbox_id: str,
        reason_code: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE unsloth_model_import_completion_outbox
                SET attempts = attempts + 1,
                    last_error = ?,
                    updated_at = ?
                WHERE outbox_id = ?
                  AND state = 'pending'
                """,
                (
                    str(reason_code or "")[:160],
                    time.time(),
                    str(outbox_id or ""),
                ),
            )

    @staticmethod
    def _outbox_entry(
        row: Sequence[Any],
    ) -> ModelImportCompletionOutboxEntry:
        projection = json.loads(str(row[4]))
        if not isinstance(projection, Mapping):
            raise UnslothModelCatalogError(
                "model_import_completion_outbox_corrupt",
                "The completion outbox projection is invalid.",
            )
        return ModelImportCompletionOutboxEntry(
            outbox_id=str(row[0]),
            task_id=str(row[1]),
            catalog_revision=int(row[2]),
            response_sha256=str(row[3]),
            projection=dict(projection),
            state=str(row[5]),
            publishes_catalog=bool(row[6]),
            attempts=int(row[7]),
            last_error=(
                str(row[8])
                if row[8] is not None
                else None
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path), timeout=10)


class UnslothModelImportResultHandler:
    """Validate a worker result and append one catalog metadata version."""

    _RESULT_FIELDS = frozenset(
        {
            "schema",
            "cache_key",
            "relative_path",
            "content_sha256",
            "file_count",
            "total_bytes",
        }
    )

    def __init__(self, registry: ImportedModelCatalogPort) -> None:
        self._registry = registry

    def handle(
        self,
        *,
        task_id: str,
        task_payload: Mapping[str, Any],
        worker_result: Mapping[str, Any],
    ) -> ImportedModelVersion:
        return self._registry.promote(
            self._validated_metadata(
                task_id=task_id,
                task_payload=task_payload,
                worker_result=worker_result,
            )
        )

    def handle_with_completion_outbox(
        self,
        *,
        task_id: str,
        task_payload: Mapping[str, Any],
        worker_result: Mapping[str, Any],
        worker_envelope: Mapping[str, Any],
    ) -> tuple[
        ImportedModelVersion,
        ModelImportCompletionOutboxEntry,
    ]:
        return self._registry.promote_with_completion_outbox(
            self._validated_metadata(
                task_id=task_id,
                task_payload=task_payload,
                worker_result=worker_result,
            ),
            task_id=task_id,
            worker_envelope=worker_envelope,
        )

    def _validated_metadata(
        self,
        *,
        task_id: str,
        task_payload: Mapping[str, Any],
        worker_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(worker_result) != self._RESULT_FIELDS or worker_result.get("schema") != (
            "ananta.unsloth-model-import-result.v1"
        ):
            raise UnslothModelCatalogError(
                "model_import_result_invalid",
                "Worker result does not match the closed import-result contract.",
            )
        if task_payload.get("schema_version") != 2:
            raise UnslothModelCatalogError("model_import_task_invalid", "Import task schema is invalid.")
        content_sha256 = str(worker_result.get("content_sha256") or "")
        cache_key = str(worker_result.get("cache_key") or "")
        relative_path = str(
            worker_result.get("relative_path") or ""
        )
        if content_sha256 != task_payload.get("expected_sha256"):
            raise UnslothModelCatalogError(
                "model_import_result_hash_mismatch",
                "Worker result is not bound to the admitted snapshot hash.",
            )
        if (
            len(cache_key) != 64
            or any(
                character not in "0123456789abcdef"
                for character in cache_key
            )
            or relative_path != cache_key
        ):
            raise UnslothModelCatalogError(
                "model_import_result_reference_invalid",
                (
                    "Worker cache references are not immutable "
                    "root-relative identifiers."
                ),
            )
        total_bytes = worker_result.get("total_bytes")
        file_count = worker_result.get("file_count")
        if (
            isinstance(total_bytes, bool)
            or not isinstance(total_bytes, int)
            or not 0 < total_bytes <= int(task_payload.get("max_bytes") or 0)
            or isinstance(file_count, bool)
            or not isinstance(file_count, int)
            or file_count < 1
        ):
            raise UnslothModelCatalogError(
                "model_import_result_size_invalid",
                "Worker result exceeds admitted size or file-count bounds.",
            )
        source_id = normalize_source_ids([task_payload.get("source_id")])[0]
        model_id = str(task_payload.get("model_id") or task_payload.get("artifact_id") or "")
        immutable_revision = str(
            task_payload.get("revision")
            or f"artifact:{task_payload.get('artifact_id')}:{content_sha256}"
        )
        return {
                "tenant_id": task_payload.get("tenant_id"),
                "model_id": model_id,
                "display_name": model_id,
                "source_id": source_id,
                "immutable_revision": immutable_revision,
                "snapshot_sha256": str(task_payload.get("expected_sha256") or ""),
                "content_sha256": content_sha256,
                "license_status": task_payload.get("license_status"),
                "format": task_payload.get("format"),
                "size_bytes": total_bytes,
                "architecture": task_payload.get("architecture"),
                "quantization": task_payload.get("quantization"),
                "capability_facets": list(task_payload.get("capability_facets") or ()),
                "import_task_id": task_id,
        }


def get_unsloth_model_catalog_registry(
    *,
    artifact_root: str | Path | None = None,
) -> SqliteUnslothModelCatalogRegistry:
    """Return the process-local Hub registry, never a Worker cache."""

    if not has_app_context():
        raise RuntimeError(
            "unsloth_model_catalog_app_context_required"
        )
    configured = current_app.extensions.get(
        "unsloth_model_catalog_registry"
    )
    if isinstance(
        configured,
        SqliteUnslothModelCatalogRegistry,
    ):
        return configured
    explicit = str(
        os.environ.get(
            "ANANTA_UNSLOTH_MODEL_CATALOG_PATH"
        )
        or ""
    ).strip()
    if explicit:
        path = Path(explicit)
    elif artifact_root is not None:
        path = (
            Path(artifact_root)
            / ".control"
            / "unsloth-model-catalog.sqlite3"
        )
    else:
        path = (
            Path(
                str(
                    current_app.config.get("DATA_DIR")
                    or "data"
                )
            )
            / "unsloth-control"
            / "model-catalog.sqlite3"
        )
    registry = SqliteUnslothModelCatalogRegistry(path)
    current_app.extensions[
        "unsloth_model_catalog_registry"
    ] = registry
    return registry


def _normalize_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "tenant_id",
        "model_id",
        "display_name",
        "source_id",
        "immutable_revision",
        "snapshot_sha256",
        "content_sha256",
        "license_status",
        "format",
        "size_bytes",
        "architecture",
        "quantization",
        "capability_facets",
        "import_task_id",
    }
    if set(value) != allowed:
        raise UnslothModelCatalogError("imported_model_metadata_invalid", "Catalog metadata is not closed.")
    facets = value.get("capability_facets")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        raise UnslothModelCatalogError(
            "imported_model_capability_facets_invalid",
            "Capability facets must be a bounded array.",
        )
    facet_values = tuple(str(item) for item in facets)
    if len(set(facet_values)) != len(facet_values):
        raise UnslothModelCatalogError(
            "imported_model_capability_facets_invalid",
            "Capability facets must not contain duplicates.",
        )
    normalized = {
        **dict(value),
        "source_id": normalize_source_ids([value.get("source_id")])[0],
        "capability_facets": [
            {"id": facet, "available": True, "reason_code": None}
            for facet in sorted(facet_values)
        ],
    }
    if normalized["license_status"] != "approved":
        raise UnslothModelCatalogError(
            "imported_model_license_not_approved",
            "Only approved model metadata can be promoted.",
        )
    return normalized
