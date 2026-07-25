from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from worker.retrieval.qdrant_client_port import QdrantClientError, QdrantClientPort
from worker.retrieval.qdrant_collection_schema import (
    RECORD_TYPE_KEY,
    RECORD_TYPE_MANIFEST,
    CompatibilityReport,
    QdrantSchemaError,
    collection_alias,
    compare_compatibility,
    manifest_client_point,
    manifest_point_id,
    scope_matches_payload,
    versioned_collection_name,
)
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope


@dataclass(frozen=True, slots=True)
class CollectionActivation:
    alias_name: str
    previous_collection: str | None
    active_collection: str


class QdrantCollectionManager:
    def __init__(
        self,
        client: QdrantClientPort,
        *,
        collection_prefix: str = "ananta",
        clock: Callable[[], float] = time.time,
    ):
        self._client = client
        self._prefix = str(collection_prefix or "ananta")
        self._clock = clock

    @property
    def client(self) -> QdrantClientPort:
        return self._client

    def alias_name(self, scope: VectorScope) -> str:
        return collection_alias(self._prefix, scope)

    def active_collection(self, scope: VectorScope) -> str | None:
        return self._client.resolve_alias(self.alias_name(scope))

    def create_versioned(
        self,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        *,
        index_version: str,
    ) -> str:
        collection_name = versioned_collection_name(self._prefix, scope, index_version)
        info = self._client.collection_info(collection_name)
        if info is None:
            self._client.create_collection(
                collection_name,
                dimensions=int(compatibility.dimensions),
                distance=str(compatibility.distance),
            )
            self._client.upsert(
                collection_name,
                [
                    manifest_client_point(
                        collection_name,
                        scope,
                        compatibility,
                        created_at_epoch=self._clock(),
                    )
                ],
            )
            return collection_name
        if int(info.dimensions) != int(compatibility.dimensions):
            raise QdrantSchemaError("dimensions_mismatch")
        if str(info.distance).lower() != str(compatibility.distance).lower():
            raise QdrantSchemaError("distance_mismatch")
        report = self.compatibility(collection_name, compatibility)
        if not report.compatible:
            raise QdrantSchemaError(report.reason)
        return collection_name

    def _manifest_payload(self, collection_name: str) -> dict:
        points = self._client.retrieve(collection_name, [manifest_point_id(collection_name)])
        if len(points) != 1:
            return {}
        payload = dict(points[0].payload or {})
        if payload.get(RECORD_TYPE_KEY) != RECORD_TYPE_MANIFEST:
            return {}
        return payload

    def compatibility(
        self,
        collection_name: str,
        expected: CompatibilitySpec,
    ) -> CompatibilityReport:
        payload = self._manifest_payload(collection_name)
        return compare_compatibility(expected, dict(payload.get("compatibility") or {}))

    def query_compatibility(
        self,
        collection_name: str,
        *,
        scope: VectorScope,
        dimensions: int,
        distance: str,
    ) -> CompatibilityReport:
        """Validate query-known fields without inventing embedding metadata."""

        payload = self._manifest_payload(collection_name)
        found = dict(payload.get("compatibility") or {})
        manifest_scope = dict(payload.get("scope") or {})
        if not found:
            return CompatibilityReport(
                False,
                "migration_required",
                {"dimensions": int(dimensions), "distance": str(distance)},
                {},
            )
        if not scope_matches_payload(scope, manifest_scope):
            return CompatibilityReport(
                False,
                "vector_scope_conflict",
                {"scope": scope.as_dict()},
                {"scope": manifest_scope},
            )
        try:
            expected = CompatibilitySpec(
                dimensions=int(dimensions),
                distance=str(distance),
                provider=str(found.get("provider") or ""),
                model=str(found.get("model") or ""),
                profile=str(found.get("profile") or ""),
                encoding=str(found.get("encoding") or ""),
                config_hash=str(found.get("config_hash") or ""),
                schema_version=str(found.get("schema_version") or ""),
                manifest_hash=str(found.get("manifest_hash") or ""),
            )
        except (TypeError, ValueError):
            return CompatibilityReport(
                False,
                "migration_required",
                {"dimensions": int(dimensions), "distance": str(distance)},
                found,
            )
        return compare_compatibility(expected, found)

    def manifest_scope_matches(self, collection_name: str, scope: VectorScope) -> bool:
        payload = self._manifest_payload(collection_name)
        manifest_scope = dict(payload.get("scope") or {})
        return scope_matches_payload(scope, manifest_scope)

    def record_count(self, collection_name: str) -> int:
        info = self._client.collection_info(collection_name)
        return max(0, int(info.points_count if info else 0) - 1)

    def activate(
        self,
        scope: VectorScope,
        collection_name: str,
        expected: CompatibilitySpec,
    ) -> CollectionActivation:
        if not self.manifest_scope_matches(collection_name, scope):
            raise QdrantSchemaError("vector_scope_conflict")
        report = self.compatibility(collection_name, expected)
        if not report.compatible:
            raise QdrantSchemaError(report.reason)
        alias = self.alias_name(scope)
        previous = self._client.resolve_alias(alias)
        self._client.swap_alias(alias, collection_name)
        return CollectionActivation(alias, previous, collection_name)

    def cleanup_inactive(self, scope: VectorScope, *, retain: int = 2) -> tuple[str, ...]:
        retain_count = max(0, int(retain))
        alias = self.alias_name(scope)
        active = self._client.resolve_alias(alias)
        candidates: list[tuple[float, str]] = []
        for collection_name in self._client.list_collections(prefix=f"{alias}-"):
            if collection_name == active:
                continue
            payload = self._manifest_payload(collection_name)
            if not payload or not self.manifest_scope_matches(collection_name, scope):
                continue
            candidates.append((float(payload.get("created_at_epoch") or 0.0), collection_name))
        candidates.sort(reverse=True)
        removed: list[str] = []
        for _, collection_name in candidates[retain_count:]:
            self._client.delete_collection(collection_name)
            removed.append(collection_name)
        return tuple(removed)

    def discard_staging(self, collection_name: str) -> None:
        try:
            self._client.delete_collection(collection_name)
        except QdrantClientError:
            # The active alias has not moved; cleanup can be retried by retention.
            return
