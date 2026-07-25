from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from worker.retrieval.qdrant_client_port import (
    COLLECTION_MISSING,
    QDRANT_UNAVAILABLE,
    QdrantClientAdapter,
    QdrantClientError,
    QdrantClientPort,
)
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import (
    VECTOR_POINT_SCHEMA_VERSION,
    QdrantSchemaError,
    compatibility_fingerprint,
    deterministic_point_id,
    scope_matches_payload,
    to_client_point,
    unique_point_count,
)
from worker.retrieval.qdrant_filter_builder import QdrantFilterBuilder
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStoreDiagnostic,
    VectorStoreFilters,
)
from worker.retrieval.vector_store_config import QdrantVectorStoreConfig
from worker.retrieval.vector_store_endpoint_policy import SecretReference


def _public_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(payload or {}).items()
        if not str(key).startswith("_")
        and str(key) not in {"source_hash", "config_hash", "embedding_text", "vector"}
    }


def _resolve_secret(secret_resolver: Any, reference: Any) -> str | None:
    if reference is None:
        return None
    if secret_resolver is None:
        raise QdrantClientError("vector_store_secret_resolver_required", operation="resolve_secret")
    parsed_reference = (
        reference
        if isinstance(reference, SecretReference)
        else SecretReference.parse(str(reference))
    )
    if callable(secret_resolver):
        value = secret_resolver(parsed_reference)
    else:
        value = secret_resolver.resolve(parsed_reference)
    get_secret_value = getattr(value, "get_secret_value", None)
    if callable(get_secret_value):
        value = get_secret_value()
    return str(value or "") or None


def emit_operation_observation(
    observer: Any,
    *,
    operation: str,
    outcome: str,
    reason: str,
    duration_seconds: float,
    counts: Mapping[str, int] | None = None,
    requested_backend: str | None = None,
    effective_backend: str | None = None,
    provider_fallback: bool = False,
) -> None:
    if observer is None:
        return


def observation_outcome(status: str) -> str:
    return {
        "ok": "success",
        "success": "success",
        "partial": "degraded",
        "degraded": "degraded",
        "failed": "failed",
        "skipped": "skipped",
    }.get(str(status or "").lower(), "failed")
    try:
        from worker.retrieval.vector_store_observer import VectorStoreOperationObservation

        observation = VectorStoreOperationObservation(
            backend="qdrant",
            operation=operation,
            outcome=outcome,
            reason_code=reason,
            duration_seconds=max(0.0, float(duration_seconds)),
            counts=dict(counts or {}),
            requested_backend=requested_backend,
            effective_backend=effective_backend,
            provider_fallback=provider_fallback,
        )
        observer.observe(observation)
    except (ImportError, TypeError, ValueError):
        # Observability is optional and must never alter retrieval behavior.
        return


class QdrantVectorStore:
    backend_version = "qdrant-1.18"

    def __init__(
        self,
        *,
        client: QdrantClientPort,
        collection_manager: QdrantCollectionManager,
        distance: str = "cosine",
        schema_version: str = VECTOR_POINT_SCHEMA_VERSION,
        retention_collections: int = 2,
        filter_builder: QdrantFilterBuilder | None = None,
        observer: Any = None,
    ):
        self._client = client
        self._manager = collection_manager
        self._distance = str(distance or "cosine").lower()
        self._schema_version = str(schema_version or VECTOR_POINT_SCHEMA_VERSION)
        self._retention_collections = max(1, int(retention_collections))
        self._filters = filter_builder or QdrantFilterBuilder()
        self._observer = observer

    @classmethod
    def from_config(
        cls,
        config: QdrantVectorStoreConfig,
        *,
        secret_resolver: Any = None,
        observer: Any = None,
    ) -> "QdrantVectorStore":
        endpoint = config.endpoint
        api_key = _resolve_secret(secret_resolver, getattr(endpoint, "api_key_ref", None))
        client = QdrantClientAdapter.from_endpoint(endpoint, api_key=api_key)
        manager = QdrantCollectionManager(
            client,
            collection_prefix=str(config.collection_prefix),
        )
        return cls(
            client=client,
            collection_manager=manager,
            distance=str(getattr(config.distance, "value", config.distance)),
            schema_version=str(config.schema_version),
            retention_collections=int(config.retention_collections),
            observer=observer,
        )

    @property
    def collection_manager(self) -> QdrantCollectionManager:
        return self._manager

    def diagnostics(self) -> VectorStoreDiagnostic:
        started = time.monotonic()
        availability = self._client.probe()
        ready = availability.status == "ready"
        diagnostic = VectorStoreDiagnostic(
            status="ready" if ready else "degraded",
            reason=availability.reason,
            provider="qdrant",
            backend_version=self.backend_version,
            details={"distance": self._distance},
        )
        emit_operation_observation(
            self._observer,
            operation="diagnostics",
            outcome="success" if ready else "degraded",
            reason=availability.reason,
            duration_seconds=time.monotonic() - started,
        )
        return diagnostic

    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult:
        started = time.monotonic()
        reason = "ok"
        outcome = "success"
        hits: tuple[VectorSearchHit, ...] = ()
        diagnostics: dict[str, Any] = {}
        try:
            if query.scope is None:
                raise QdrantSchemaError("vector_scope_required")
            top_k = int(query.top_k)
            if not 1 <= top_k <= 1000:
                raise QdrantSchemaError("vector_top_k_invalid")
            vector = tuple(float(value) for value in query.query_vector)
            if not vector:
                raise QdrantSchemaError("dimensions_mismatch")
            collection = self._manager.active_collection(query.scope)
            if collection is None:
                reason = "empty_collection"
                diagnostics = {"status": "ready", "reason": reason}
            else:
                report = self._manager.query_compatibility(
                    collection,
                    scope=query.scope,
                    dimensions=len(vector),
                    distance=self._distance,
                )
                if not report.compatible:
                    reason = report.reason
                    outcome = "degraded"
                    diagnostics = {"status": "degraded", "reason": reason}
                else:
                    server_filter = self._filters.build(
                        scope=query.scope,
                        filters=query.filters,
                    )
                    scored = self._client.query_points(
                        collection,
                        query_vector=vector,
                        query_filter=server_filter,
                        limit=top_k,
                    )
                    hits = tuple(
                        VectorSearchHit(
                            record_id=str(point.payload.get("record_id") or point.point_id),
                            score=float(point.score),
                            payload=_public_payload(point.payload),
                        )
                        for point in scored
                    )
                    diagnostics = {
                        "status": "ready",
                        "reason": "ok",
                        "hits": len(hits),
                        "top_k": top_k,
                    }
        except (QdrantSchemaError, QdrantClientError) as exc:
            reason = exc.reason
            outcome = "degraded"
            diagnostics = {"status": "degraded", "reason": reason}
        result = VectorSearchResult(
            hits=hits,
            diagnostics=diagnostics,
            requested_provider="qdrant",
            effective_provider="qdrant",
            provider_fallback=False,
            reason=reason,
        )
        emit_operation_observation(
            self._observer,
            operation="search",
            outcome=outcome,
            reason=reason,
            duration_seconds=time.monotonic() - started,
            counts={"hits": len(hits)},
        )
        return result

    @staticmethod
    def _one_scope(points: Sequence[PreparedVectorPoint]) -> VectorScope:
        if not points:
            raise QdrantSchemaError("prepared_points_required")
        scope = points[0].scope
        for point in points:
            if point.scope != scope:
                raise QdrantSchemaError("vector_scope_conflict")
        return scope

    def _upsert_to_collection(
        self,
        collection_name: str,
        points: Sequence[PreparedVectorPoint],
        compatibility: CompatibilitySpec,
        *,
        batch_size: int,
    ) -> IndexWriteResult:
        size = int(batch_size)
        if not 1 <= size <= 1000:
            return IndexWriteResult(
                status="failed",
                mode="upsert",
                reason="vector_batch_size_invalid",
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": "vector_batch_size_invalid"},
                failed=len(points),
            )
        upserted = skipped = failed = 0
        reasons: list[str] = []
        for offset in range(0, len(points), size):
            batch = list(points[offset : offset + size])
            try:
                client_points = [to_client_point(point, compatibility) for point in batch]
                existing = {
                    point.point_id: point
                    for point in self._client.retrieve(
                        collection_name,
                        [point.point_id for point in client_points],
                    )
                }
                changed = []
                for point in client_points:
                    current = existing.get(point.point_id)
                    if current and str(current.payload.get("source_hash") or "") == str(
                        point.payload.get("source_hash") or ""
                    ):
                        skipped += 1
                    else:
                        changed.append(point)
                self._client.upsert(collection_name, changed)
                upserted += len(changed)
            except (QdrantSchemaError, QdrantClientError) as exc:
                failed += len(batch)
                reasons.append(exc.reason)
        status = "ok" if failed == 0 else ("partial" if upserted or skipped else "failed")
        reason = "upserted" if failed == 0 else reasons[0]
        return IndexWriteResult(
            status=status,
            mode="upsert",
            reason=reason,
            indexed_documents=upserted,
            diagnostics={
                "status": status,
                "reason": reason,
                "batch_size": size,
                "errors": tuple(sorted(set(reasons))),
            },
            upserted=upserted,
            skipped=skipped,
            failed=failed,
        )

    def upsert(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        started = time.monotonic()
        point_list = list(points)
        try:
            scope = self._one_scope(point_list)
            collection = self._manager.active_collection(scope)
            if collection is None:
                raise QdrantSchemaError(COLLECTION_MISSING)
            manifest = self._manager._manifest_payload(collection)
            compatibility_data = dict(manifest.get("compatibility") or {})
            compatibility = CompatibilitySpec(**compatibility_data)
            result = self._upsert_to_collection(
                collection,
                point_list,
                compatibility,
                batch_size=batch_size,
            )
        except (QdrantSchemaError, QdrantClientError, TypeError) as exc:
            reason = getattr(exc, "reason", "migration_required")
            result = IndexWriteResult(
                status="failed",
                mode="upsert",
                reason=reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": reason},
                failed=len(point_list),
            )
        emit_operation_observation(
            self._observer,
            operation="upsert",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "upserted": result.upserted,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        )
        return result

    def rebuild(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        started = time.monotonic()
        point_list = list(points)
        staging: str | None = None
        try:
            scope = self._one_scope(point_list)
            index_version = str(
                compatibility.manifest_hash or compatibility_fingerprint(compatibility)
            )
            staging = self._manager.create_versioned(
                scope,
                compatibility,
                index_version=index_version,
            )
            write = self._upsert_to_collection(
                staging,
                point_list,
                compatibility,
                batch_size=128,
            )
            if write.failed:
                raise QdrantSchemaError(write.reason)
            if self._manager.record_count(staging) != unique_point_count(point_list):
                raise QdrantSchemaError("point_count_mismatch")
            activation = self._manager.activate(scope, staging, compatibility)
            self._manager.cleanup_inactive(
                scope,
                retain=self._retention_collections,
            )
            result = replace(
                write,
                mode="rebuild",
                reason="rebuild",
                diagnostics={
                    **dict(write.diagnostics),
                    "alias_changed": activation.previous_collection != activation.active_collection,
                },
            )
        except (QdrantSchemaError, QdrantClientError) as exc:
            if staging is not None:
                self._manager.discard_staging(staging)
            result = IndexWriteResult(
                status="failed",
                mode="rebuild",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(point_list),
            )
        emit_operation_observation(
            self._observer,
            operation="rebuild",
            outcome="success" if result.status == "ok" else "failed",
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={"upserted": result.upserted, "failed": result.failed},
        )
        return result

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        started = time.monotonic()
        point_list = list(points)
        try:
            scope = self._one_scope(point_list)
            collection = self._manager.active_collection(scope)
            if collection is None:
                result = self.rebuild(point_list, compatibility=compatibility)
            else:
                report = self._manager.compatibility(collection, compatibility)
                if not report.compatible:
                    result = self.rebuild(point_list, compatibility=compatibility)
                else:
                    write = self._upsert_to_collection(
                        collection,
                        point_list,
                        compatibility,
                        batch_size=128,
                    )
                    result = replace(
                        write,
                        mode="refresh",
                        reason=(
                            "unchanged"
                            if write.upserted == 0 and write.failed == 0
                            else "refreshed"
                        ),
                    )
        except (QdrantSchemaError, QdrantClientError) as exc:
            result = IndexWriteResult(
                status="failed",
                mode="refresh",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(point_list),
            )
        emit_operation_observation(
            self._observer,
            operation="refresh",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "upserted": result.upserted,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        )
        return result

    def delete(self, point_ids: Sequence[str], *, scope: VectorScope) -> IndexWriteResult:
        started = time.monotonic()
        requested = [str(value) for value in point_ids if str(value)]
        try:
            collection = self._manager.active_collection(scope)
            if collection is None:
                raise QdrantSchemaError(COLLECTION_MISSING)
            normalised = [
                value
                if _is_uuid(value)
                else deterministic_point_id(scope, value)
                for value in requested
            ]
            existing = self._client.retrieve(collection, normalised)
            safe = [
                point.point_id
                for point in existing
                if scope_matches_payload(scope, point.payload)
            ]
            rejected = len(existing) - len(safe)
            self._client.delete_points(collection, safe)
            result = IndexWriteResult(
                status="ok" if rejected == 0 else "partial",
                mode="delete",
                reason="deleted" if rejected == 0 else "vector_scope_conflict",
                indexed_documents=0,
                diagnostics={"status": "ok" if rejected == 0 else "partial"},
                deleted=len(safe),
                failed=rejected,
            )
        except (QdrantSchemaError, QdrantClientError) as exc:
            result = IndexWriteResult(
                status="failed",
                mode="delete",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(requested),
            )
        emit_operation_observation(
            self._observer,
            operation="delete",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={"deleted": result.deleted, "failed": result.failed},
        )
        return result

    def delete_scope(self, scope: VectorScope) -> IndexWriteResult:
        collection = self._manager.active_collection(scope)
        if collection is None:
            return IndexWriteResult(
                status="ok",
                mode="delete",
                reason="empty_collection",
                indexed_documents=0,
                diagnostics={"status": "ready", "reason": "empty_collection"},
            )
        before = self._manager.record_count(collection)
        self._client.delete_by_filter(collection, self._filters.scope_only(scope))
        return IndexWriteResult(
            status="ok",
            mode="delete",
            reason="deleted",
            indexed_documents=0,
            diagnostics={"status": "ok"},
            deleted=before,
        )

    def rename(
        self,
        old_point_id: str,
        replacement: PreparedVectorPoint,
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        write = self.upsert([replacement], batch_size=batch_size)
        if write.status != "ok":
            return replace(write, mode="rename")
        deleted = self.delete([old_point_id], scope=replacement.scope)
        return IndexWriteResult(
            status=deleted.status,
            mode="rename",
            reason="renamed" if deleted.status == "ok" else deleted.reason,
            indexed_documents=write.indexed_documents,
            diagnostics={
                "upsert": dict(write.diagnostics),
                "delete": dict(deleted.diagnostics),
            },
            upserted=write.upserted,
            deleted=deleted.deleted,
            skipped=write.skipped,
            failed=write.failed + deleted.failed,
        )

    def prepare_collection(
        self,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        *,
        index_version: str,
    ) -> str:
        return self._manager.create_versioned(
            scope,
            compatibility,
            index_version=index_version,
        )

    def upsert_to_collection(
        self,
        collection_name: str,
        points: Sequence[PreparedVectorPoint],
        compatibility: CompatibilitySpec,
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        return self._upsert_to_collection(
            collection_name,
            list(points),
            compatibility,
            batch_size=batch_size,
        )

    def activate_collection(
        self,
        scope: VectorScope,
        collection_name: str,
        compatibility: CompatibilitySpec,
    ) -> None:
        self._manager.activate(scope, collection_name, compatibility)

    def close(self) -> None:
        started = time.monotonic()
        outcome = "success"
        reason = "closed"
        try:
            self._client.close()
        except QdrantClientError as exc:
            outcome = "failed"
            reason = exc.reason
        emit_operation_observation(
            self._observer,
            operation="close",
            outcome=outcome,
            reason=reason,
            duration_seconds=time.monotonic() - started,
        )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return False
    return True
