from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

from worker.retrieval.qdrant_client_port import (
    COLLECTION_MISSING,
    QdrantClientAdapter,
    QdrantClientError,
    QdrantClientPort,
)
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import (
    QDRANT_BACKEND_SCHEMA_VERSION,
    QdrantSchemaError,
    canonical_scope,
    compatibility_diagnostics,
    compatibility_fingerprint,
    deterministic_point_id,
    normalise_embedding_text,
    sanitise_payload_metadata,
    scope_matches_payload,
    to_client_point,
    unique_point_count,
)
from worker.retrieval.qdrant_filter_builder import QdrantFilterBuilder
from worker.retrieval.vector_store_config import QdrantVectorStoreConfig
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorIndexWritePlan,
    VectorScope,
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStoreDiagnostic,
)
from worker.retrieval.vector_store_endpoint_policy import SecretReference
from worker.retrieval.vector_store_observer import (
    bounded_vector_store_reason,
    emit_operation_observation,
    observation_outcome,
)


def _public_payload(
    payload: Mapping[str, Any],
    *,
    include_embedding_text: bool = False,
) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for raw_key, value in dict(payload or {}).items():
        key = str(raw_key)
        if key.startswith("_") or key in {"source_hash", "config_hash", "vector"}:
            continue
        if key == "embedding_text":
            if include_embedding_text:
                embedding_text = normalise_embedding_text(value)
                if embedding_text:
                    public[key] = embedding_text
            continue
        if key == "metadata":
            public[key] = sanitise_payload_metadata(value)
            continue
        public[key] = value
    return public


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


class QdrantVectorStore:
    backend_version = "qdrant-1.18"

    def __init__(
        self,
        *,
        client: QdrantClientPort,
        collection_manager: QdrantCollectionManager,
        distance: str = "cosine",
        schema_version: str = QDRANT_BACKEND_SCHEMA_VERSION,
        retention_collections: int = 2,
        filter_builder: QdrantFilterBuilder | None = None,
        observer: Any = None,
        store_embedding_text: bool = False,
        compatibility_resolver: Callable[[VectorScope], CompatibilitySpec | None] | None = None,
    ):
        self._client = client
        self._manager = collection_manager
        self._distance = str(distance or "cosine").lower()
        self._schema_version = str(schema_version or QDRANT_BACKEND_SCHEMA_VERSION)
        if self._schema_version != collection_manager.backend_schema_version:
            raise QdrantSchemaError("vector_store_backend_schema_conflict")
        self._retention_collections = max(1, int(retention_collections))
        self._filters = filter_builder or QdrantFilterBuilder()
        self._observer = observer
        self._store_embedding_text = bool(store_embedding_text)
        self._compatibility_resolver = compatibility_resolver
        self._known_compatibilities: dict[str, CompatibilitySpec] = {}
        self._compatibility_lock = threading.RLock()

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
        tls_ca_cert_pem = _resolve_secret(
            secret_resolver,
            getattr(endpoint, "tls_ca_cert_ref", None),
        )
        client = QdrantClientAdapter.from_endpoint(
            endpoint,
            api_key=api_key,
            tls_ca_cert_pem=tls_ca_cert_pem,
        )
        manager = QdrantCollectionManager(
            client,
            collection_prefix=str(config.collection_prefix),
            backend_schema_version=str(config.schema_version),
        )
        return cls(
            client=client,
            collection_manager=manager,
            distance=str(getattr(config.distance, "value", config.distance)),
            schema_version=str(config.schema_version),
            retention_collections=int(config.retention_collections),
            observer=observer,
            store_embedding_text=bool(config.store_embedding_text),
        )

    @property
    def collection_manager(self) -> QdrantCollectionManager:
        return self._manager

    def _remember_compatibility(
        self,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
    ) -> None:
        with self._compatibility_lock:
            self._known_compatibilities[canonical_scope(scope)] = compatibility

    def _query_compatibility(
        self,
        query: VectorSearchQuery,
    ) -> CompatibilitySpec | None:
        if query.compatibility is not None:
            return query.compatibility
        if query.scope is None:
            return None
        key = canonical_scope(query.scope)
        with self._compatibility_lock:
            known = self._known_compatibilities.get(key)
        if known is not None:
            return known
        if self._compatibility_resolver is None:
            return None
        try:
            resolved = self._compatibility_resolver(query.scope)
        except Exception:
            return None
        return resolved if isinstance(resolved, CompatibilitySpec) else None

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
        top_k = int(query.top_k)
        try:
            if query.scope is None:
                raise QdrantSchemaError("vector_scope_required")
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
                    expected=self._query_compatibility(query),
                    dimensions=len(vector),
                    distance=self._distance,
                )
                if not report.compatible:
                    reason = report.reason
                    outcome = "degraded"
                    diagnostics = {
                        "status": "degraded",
                        "reason": reason,
                        "compatibility": compatibility_diagnostics(report),
                    }
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
                            payload=_public_payload(
                                point.payload,
                                include_embedding_text=self._store_embedding_text,
                            ),
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
            counts={"top_k": top_k, "hits": len(hits)},
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
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 1000
        ):
            return IndexWriteResult(
                status="failed",
                mode="upsert",
                reason="vector_batch_size_invalid",
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": "vector_batch_size_invalid"},
                failed=len(points),
                accepted=0,
            )
        size = batch_size
        try:
            scope = self._one_scope(points)
            compatibility_report = self._manager.query_compatibility(
                collection_name,
                scope=scope,
                expected=compatibility,
                dimensions=len(points[0].vector),
                distance=self._distance,
            )
            if not compatibility_report.compatible:
                raise QdrantSchemaError(
                    compatibility_report.reason
                )
            unique_point_count(points)
        except (QdrantClientError, QdrantSchemaError) as exc:
            return IndexWriteResult(
                status="failed",
                mode="upsert",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(points),
                accepted=0,
            )
        upserted = skipped = failed = 0
        reasons: list[str] = []
        failure_batches: list[dict[str, int | str]] = []
        total_failure_batches = 0
        max_failure_diagnostics = 32
        for batch_index, offset in enumerate(range(0, len(points), size)):
            batch = list(points[offset : offset + size])
            try:
                client_points = [
                    to_client_point(
                        point,
                        compatibility,
                        store_embedding_text=self._store_embedding_text,
                    )
                    for point in batch
                ]
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
                    incoming_source_hash = str(
                        point.payload.get("source_hash") or ""
                    ).strip()
                    if not incoming_source_hash:
                        raise QdrantSchemaError("missing_source_hash")
                    current_source_hash = (
                        str(current.payload.get("source_hash") or "").strip()
                        if current is not None
                        else ""
                    )
                    current_embedding_text = (
                        current.payload.get("embedding_text")
                        if current is not None
                        else None
                    )
                    incoming_embedding_text = point.payload.get("embedding_text")
                    if current and current_source_hash and (
                        current_source_hash == incoming_source_hash
                        and current_embedding_text == incoming_embedding_text
                    ):
                        skipped += 1
                    else:
                        changed.append(point)
                if changed:
                    self._client.upsert(collection_name, changed)
                upserted += len(changed)
            except (QdrantSchemaError, QdrantClientError) as exc:
                failed += len(batch)
                reason_code = bounded_vector_store_reason(exc.reason)
                reasons.append(reason_code)
                total_failure_batches += 1
                if len(failure_batches) < max_failure_diagnostics:
                    failure_batches.append(
                        {
                            "batch_index": batch_index,
                            "reason_code": reason_code,
                        }
                    )
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
                "failure_batches": tuple(failure_batches),
                "total_failure_batches": total_failure_batches,
                "failure_batches_truncated": (
                    total_failure_batches > len(failure_batches)
                ),
            },
            upserted=upserted,
            skipped=skipped,
            failed=failed,
            accepted=len(points),
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
            unique_point_count(point_list)
            collection = self._manager.active_collection(scope)
            if collection is None:
                raise QdrantSchemaError(COLLECTION_MISSING)
            manifest = self._manager._manifest_payload(collection)
            compatibility_data = dict(manifest.get("compatibility") or {})
            compatibility = CompatibilitySpec(**compatibility_data)
            self._remember_compatibility(scope, compatibility)
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
                accepted=(
                    0
                    if reason == "vector_point_id_mismatch"
                    else len(point_list)
                ),
            )
        emit_operation_observation(
            self._observer,
            operation="upsert",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "accepted": result.accepted,
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
        return self.rebuild_with_plan(
            points,
            compatibility=compatibility,
            plan=VectorIndexWritePlan(),
        )

    def rebuild_with_plan(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
        plan: VectorIndexWritePlan,
    ) -> IndexWriteResult:
        started = time.monotonic()
        point_list = list(points)
        staging: str | None = None
        activated = False
        try:
            scope = self._one_scope(point_list)
            expected_point_count = unique_point_count(point_list)
            index_version = str(
                compatibility.manifest_hash or compatibility_fingerprint(compatibility)
            )
            staging = self._manager.create_staging(
                scope,
                compatibility,
                index_version=index_version,
            )
            write = self._upsert_to_collection(
                staging,
                point_list,
                compatibility,
                batch_size=plan.batch_size,
            )
            if write.failed:
                raise QdrantSchemaError(write.reason)
            if self._manager.record_count(staging) != expected_point_count:
                raise QdrantSchemaError("point_count_mismatch")
            activation = self._manager.activate(scope, staging, compatibility)
            activated = True
            self._remember_compatibility(scope, compatibility)
            cleanup_reason: str | None = None
            try:
                self._manager.cleanup_inactive(
                    scope,
                    retain=self._retention_collections,
                )
            except QdrantClientError as exc:
                cleanup_reason = exc.reason
            result = replace(
                write,
                mode="rebuild",
                reason="rebuild",
                diagnostics={
                    **dict(write.diagnostics),
                    "alias_changed": activation.previous_collection != activation.active_collection,
                    "cleanup_reason": cleanup_reason,
                },
            )
        except (QdrantSchemaError, QdrantClientError) as exc:
            if staging is not None and not activated:
                self._manager.discard_staging(staging, scope=scope)
            result = IndexWriteResult(
                status="failed",
                mode="rebuild",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(point_list),
                accepted=(
                    0
                    if exc.reason == "vector_point_id_mismatch"
                    else len(point_list)
                ),
            )
        emit_operation_observation(
            self._observer,
            operation="rebuild",
            outcome="success" if result.status == "ok" else "failed",
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "accepted": result.accepted,
                "upserted": result.upserted,
                "failed": result.failed,
            },
        )
        return result

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return self.refresh_with_plan(
            points,
            compatibility=compatibility,
            plan=VectorIndexWritePlan(),
        )

    def refresh_with_plan(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
        plan: VectorIndexWritePlan,
    ) -> IndexWriteResult:
        started = time.monotonic()
        point_list = list(points)
        try:
            scope = self._one_scope(point_list)
            unique_point_count(point_list)
            collection = self._manager.active_collection(scope)
            if collection is None:
                result = self.rebuild_with_plan(
                    point_list,
                    compatibility=compatibility,
                    plan=plan,
                )
            else:
                report = self._manager.compatibility(collection, compatibility)
                if not report.compatible:
                    result = self.rebuild_with_plan(
                        point_list,
                        compatibility=compatibility,
                        plan=plan,
                    )
                else:
                    write = self._upsert_to_collection(
                        collection,
                        point_list,
                        compatibility,
                        batch_size=plan.batch_size,
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
                    if result.status in {"ok", "partial"}:
                        self._remember_compatibility(scope, compatibility)
        except (QdrantSchemaError, QdrantClientError) as exc:
            result = IndexWriteResult(
                status="failed",
                mode="refresh",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(point_list),
                accepted=(
                    0
                    if exc.reason == "vector_point_id_mismatch"
                    else len(point_list)
                ),
            )
        emit_operation_observation(
            self._observer,
            operation="refresh",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "accepted": result.accepted,
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
                accepted=len(requested),
            )
        except (QdrantSchemaError, QdrantClientError) as exc:
            result = IndexWriteResult(
                status="failed",
                mode="delete",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=len(requested),
                accepted=len(requested),
            )
        emit_operation_observation(
            self._observer,
            operation="delete",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "accepted": result.accepted,
                "deleted": result.deleted,
                "failed": result.failed,
            },
        )
        return result

    def delete_scope(self, scope: VectorScope) -> IndexWriteResult:
        started = time.monotonic()
        try:
            canonical_scope(scope)
            collection = self._manager.active_collection(scope)
            if collection is None:
                result = IndexWriteResult(
                    status="ok",
                    mode="delete",
                    reason="empty_collection",
                    indexed_documents=0,
                    diagnostics={"status": "ready", "reason": "empty_collection"},
                    accepted=1,
                )
            else:
                before = self._manager.record_count(collection)
                self._client.delete_by_filter(
                    collection,
                    self._filters.scope_only(scope),
                )
                after = self._manager.record_count(collection)
                result = IndexWriteResult(
                    status="ok",
                    mode="delete",
                    reason="deleted",
                    indexed_documents=0,
                    diagnostics={"status": "ok"},
                    deleted=max(0, before - after),
                    accepted=1,
                )
        except (QdrantSchemaError, QdrantClientError) as exc:
            result = IndexWriteResult(
                status="failed",
                mode="delete",
                reason=exc.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "reason": exc.reason},
                failed=1,
                accepted=1,
            )
        emit_operation_observation(
            self._observer,
            operation="delete",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "accepted": result.accepted,
                "deleted": result.deleted,
                "failed": result.failed,
            },
        )
        return result

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
            accepted=write.accepted + deleted.accepted,
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
        self._remember_compatibility(scope, compatibility)

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
