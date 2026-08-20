"""Exact DuckDB vector search implementing the existing VectorStore ports."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from worker.retrieval.duckdb_connection_factory import DuckDBConnectionFactory, DuckDBNotInstalledError
from worker.retrieval.duckdb_output_importer import DuckDBOutputImporter
from worker.retrieval.duckdb_snapshot_manager import DuckDBSnapshotManager
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStoreClosedError,
    VectorStoreDiagnostic,
    VectorStoreError,
    VectorStoreFailClosedError,
)

_BACKEND_VERSION = "duckdb-vector-store.v1"


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return 0.0
    return float(numerator / (left_norm * right_norm))


class DuckDBVectorStore:
    def __init__(
        self,
        *,
        config: DuckDBVectorStoreConfig,
        factory: DuckDBConnectionFactory | None = None,
        snapshot_manager: DuckDBSnapshotManager | None = None,
    ) -> None:
        self._config = config
        self._factory = factory or DuckDBConnectionFactory(config)
        self._snapshots = snapshot_manager or DuckDBSnapshotManager(config, self._factory)
        self._importer = DuckDBOutputImporter(config)
        self._closed = False
        self._last = VectorStoreDiagnostic(
            status="degraded",
            reason="not_loaded",
            provider="duckdb",
            backend_version=_BACKEND_VERSION,
        )

    @classmethod
    def from_config(cls, config: DuckDBVectorStoreConfig, **_kwargs: Any) -> "DuckDBVectorStore":
        return cls(config=config)

    def _ensure_open(self) -> None:
        if self._closed:
            raise VectorStoreClosedError()

    def diagnostics(self) -> VectorStoreDiagnostic:
        return self._last

    def close(self) -> None:
        self._closed = True
        self._factory.close_thread()

    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult:
        self._ensure_open()
        if query.scope is None:
            raise VectorStoreFailClosedError("vector_scope_required")
        try:
            connection = self._snapshots.open_active(query.scope, read_only=True)
        except (DuckDBNotInstalledError, VectorStoreError) as exc:
            self._last = VectorStoreDiagnostic(
                status="degraded",
                reason=exc.reason,
                provider="duckdb",
                backend_version=_BACKEND_VERSION,
            )
            return VectorSearchResult(
                hits=(),
                diagnostics={"status": "degraded", "reason": exc.reason},
                requested_provider="duckdb",
                effective_provider="duckdb",
                reason=exc.reason,
            )
        if query.compatibility is not None:
            self._assert_compatibility(connection, query.compatibility, query.scope)
        rows = connection.execute(
            """
            SELECT d.record_id, d.path, d.kind, d.symbol, v.embedding, v.dimensions
            FROM documents d
            JOIN vectors v ON v.record_id = d.record_id
            WHERE d.workspace_id = ? AND d.repository_id = ? AND d.profile_name = ?
              AND d.domain = ? AND d.tombstone = FALSE
            """,
            [
                query.scope.workspace_id,
                query.scope.repository_id,
                query.scope.profile_name,
                query.scope.domain,
            ],
        ).fetchall()
        if query.filters and query.filters.file_prefix:
            prefix = str(query.filters.file_prefix).strip("/")
            rows = [row for row in rows if str(row[1]).replace("\\", "/").startswith(prefix)]
        scored: list[tuple[float, Any]] = []
        for row in rows:
            embedding = list(row[4] or [])
            if len(embedding) != len(query.query_vector):
                continue
            scored.append((_cosine(query.query_vector, embedding), row))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits = tuple(
            VectorSearchHit(
                record_id=str(row[0]),
                score=score,
                payload={"path": row[1], "kind": row[2], "symbol": row[3]},
            )
            for score, row in scored[: int(query.top_k)]
        )
        self._last = VectorStoreDiagnostic(
            status="ready",
            reason="ok",
            provider="duckdb",
            backend_version=_BACKEND_VERSION,
            details={"hits": len(hits), "mode": "exact"},
        )
        return VectorSearchResult(
            hits=hits,
            diagnostics={"status": "ready", "reason": "ok", "hits": len(hits), "mode": "exact"},
            requested_provider="duckdb",
            effective_provider="duckdb",
            reason="ok",
        )

    def rebuild(self, points: Sequence[PreparedVectorPoint], *, compatibility: CompatibilitySpec) -> IndexWriteResult:
        return self._write(points, compatibility=compatibility, mode="rebuild")

    def refresh(self, points: Sequence[PreparedVectorPoint], *, compatibility: CompatibilitySpec) -> IndexWriteResult:
        return self._write(points, compatibility=compatibility, mode="refresh")

    def upsert(self, points: Sequence[PreparedVectorPoint], *, batch_size: int = 128) -> IndexWriteResult:
        if not points:
            return IndexWriteResult(status="ok", mode="upsert", reason="empty", indexed_documents=0)
        compatibility = CompatibilitySpec(dimensions=len(points[0].vector), provider="duckdb")
        return self._write(points, compatibility=compatibility, mode="upsert")

    def delete(self, point_ids: Sequence[str], *, scope: VectorScope) -> IndexWriteResult:
        self._ensure_open()
        try:
            points, compatibility = self._read_active_points(scope)
        except VectorStoreError as exc:
            if exc.reason != "duckdb_snapshot_empty":
                raise
            return IndexWriteResult(
                status="ok",
                mode="delete",
                reason="empty",
                indexed_documents=0,
                diagnostics={"deleted": 0},
            )
        deleted_ids = {str(point_id) for point_id in point_ids}
        remaining = [point for point in points if point.record_id not in deleted_ids]
        deleted = len(points) - len(remaining)
        if not deleted:
            return IndexWriteResult(
                status="ok",
                mode="delete",
                reason="empty",
                indexed_documents=len(points),
                diagnostics={"deleted": 0},
            )
        result = self._write(
            remaining,
            compatibility=compatibility,
            mode="delete",
            scope_override=scope,
        )
        return IndexWriteResult(
            status=result.status,
            mode="delete",
            reason=result.reason,
            indexed_documents=result.indexed_documents,
            diagnostics={**dict(result.diagnostics), "deleted": deleted},
        )

    def delete_scope(self, scope: VectorScope) -> IndexWriteResult:
        self._ensure_open()
        try:
            points, compatibility = self._read_active_points(scope)
        except VectorStoreError as exc:
            if exc.reason != "duckdb_snapshot_empty":
                raise
            return IndexWriteResult(
                status="ok",
                mode="delete_scope",
                reason="empty",
                indexed_documents=0,
                diagnostics={"deleted": 0},
            )
        if not points:
            return IndexWriteResult(
                status="ok",
                mode="delete_scope",
                reason="empty",
                indexed_documents=0,
            )
        result = self._write(
            (),
            compatibility=compatibility,
            mode="delete_scope",
            scope_override=scope,
        )
        return IndexWriteResult(
            status=result.status,
            mode="delete_scope",
            reason=result.reason,
            indexed_documents=0,
            diagnostics={**dict(result.diagnostics), "deleted": len(points)},
        )

    def _read_active_points(
        self,
        scope: VectorScope,
    ) -> tuple[list[PreparedVectorPoint], CompatibilitySpec]:
        connection = self._snapshots.open_active(scope, read_only=True)
        rows = connection.execute(
            """SELECT d.record_id, d.path, d.kind, d.symbol, d.text, d.source_hash,
                      v.embedding, v.model
               FROM documents d JOIN vectors v ON v.record_id = d.record_id
               WHERE d.workspace_id = ? AND d.repository_id = ? AND d.profile_name = ?
                 AND d.domain = ? AND d.tombstone = FALSE""",
            [scope.workspace_id, scope.repository_id, scope.profile_name, scope.domain],
        ).fetchall()
        if not rows:
            raise VectorStoreError("duckdb_snapshot_empty")
        meta = connection.execute(
            """SELECT manifest_hash, compatibility_fingerprint, source_revision
               FROM snapshot_meta LIMIT 1"""
        ).fetchone()
        if not meta:
            raise VectorStoreError("duckdb_snapshot_meta_missing")
        points = [
            PreparedVectorPoint(
                record_id=str(row[0]),
                vector=tuple(float(value) for value in list(row[6] or [])),
                scope=scope,
                payload={
                    "path": str(row[1] or ""),
                    "kind": str(row[2] or "record"),
                    "symbol": str(row[3] or ""),
                    "embedding_text": str(row[4] or ""),
                },
                source_hash=str(row[5] or ""),
            )
            for row in rows
        ]
        return points, CompatibilitySpec(
            dimensions=len(points[0].vector),
            provider="duckdb",
            model=str(rows[0][7] or "local"),
            config_hash=str(meta[1] or "default"),
            schema_version=str(meta[2] or "vector_store.v1"),
            manifest_hash=str(meta[0] or ""),
        )

    def _write(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
        mode: str,
        scope_override: VectorScope | None = None,
    ) -> IndexWriteResult:
        self._ensure_open()
        if not points and scope_override is None:
            return IndexWriteResult(status="ok", mode=mode, reason="empty", indexed_documents=0)
        scope = points[0].scope if points else scope_override
        if scope is None:
            raise VectorStoreError("vector_scope_required")
        version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        fingerprint = compatibility.config_hash or compatibility.manifest_hash or "default"
        staging = self._snapshots.create_staging(scope, fingerprint, version)
        connection = self._factory.connect(staging, read_only=False)
        records = []
        if mode in {"upsert", "refresh"}:
            try:
                active = self._snapshots.open_active(scope, read_only=True)
                existing = active.execute(
                    """SELECT d.record_id, d.path, d.kind, d.symbol, d.text, d.source_hash,
                              v.embedding, v.model
                       FROM documents d JOIN vectors v ON v.record_id = d.record_id
                       WHERE d.workspace_id = ? AND d.repository_id = ? AND d.profile_name = ?
                         AND d.domain = ? AND d.tombstone = FALSE""",
                    [scope.workspace_id, scope.repository_id, scope.profile_name, scope.domain],
                ).fetchall()
                records.extend(
                    {
                        "id": row[0], "path": row[1], "kind": row[2], "symbol": row[3],
                        "text": row[4], "source_hash": row[5], "embedding": list(row[6] or []),
                        "model": row[7],
                    }
                    for row in existing
                )
            except VectorStoreError as exc:
                if exc.reason != "duckdb_snapshot_missing":
                    raise
        for point in points:
            if point.scope != scope:
                raise VectorStoreError("vector_scope_conflict")
            if len(point.vector) != int(compatibility.dimensions):
                raise VectorStoreError("dimensions_mismatch")
            payload = dict(point.payload or {})
            records = [item for item in records if str(item.get("id")) != point.record_id]
            records.append(
                {
                    "id": point.record_id,
                    "path": payload.get("file") or payload.get("path") or point.record_id,
                    "kind": payload.get("kind") or "record",
                    "symbol": payload.get("symbol") or "",
                    "text": payload.get("embedding_text") or payload.get("text") or "",
                    "source_hash": point.source_hash,
                    "embedding": list(point.vector),
                    "model": compatibility.model or "local",
                }
            )
        counts = self._importer.import_records(
            connection,
            records=records,
            scope=scope,
            manifest_hash=str(compatibility.manifest_hash or ""),
        )
        self._snapshots.publish(
            staging_path=staging,
            scope=scope,
            manifest_hash=str(compatibility.manifest_hash or ""),
            compatibility_fingerprint=fingerprint,
            source_revision=str(compatibility.schema_version or ""),
        )
        self._factory.close_thread()
        self._last = VectorStoreDiagnostic(
            status="ready",
            reason="ok",
            provider="duckdb",
            backend_version=_BACKEND_VERSION,
            details=counts,
        )
        return IndexWriteResult(
            status="ok",
            mode=mode,
            reason="ok",
            indexed_documents=int(counts.get("documents") or 0),
            diagnostics=counts,
        )

    def _assert_compatibility(self, connection, compatibility: CompatibilitySpec, scope: VectorScope) -> None:
        rows = connection.execute(
            """
            SELECT workspace_id, repository_id, profile_name, domain, compatibility_fingerprint
            FROM snapshot_meta LIMIT 1
            """
        ).fetchall()
        if not rows:
            raise VectorStoreError("duckdb_snapshot_meta_missing")
        workspace_id, repository_id, profile_name, domain, fingerprint = rows[0]
        if (
            workspace_id != scope.workspace_id
            or repository_id != scope.repository_id
            or profile_name != scope.profile_name
            or domain != scope.domain
        ):
            raise VectorStoreFailClosedError("vector_scope_conflict")
        if compatibility.config_hash and fingerprint and compatibility.config_hash != fingerprint:
            raise VectorStoreError("duckdb_compatibility_mismatch")
