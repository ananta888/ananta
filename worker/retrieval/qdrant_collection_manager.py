from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable

from worker.retrieval.qdrant_client_port import QdrantClientError, QdrantClientPort
from worker.retrieval.qdrant_collection_schema import (
    QDRANT_BACKEND_SCHEMA_VERSION,
    RECORD_TYPE_KEY,
    RECORD_TYPE_MANIFEST,
    CompatibilityReport,
    QdrantSchemaError,
    collection_alias,
    compare_compatibility,
    manifest_client_point,
    manifest_point_id,
    missing_compatibility_fields,
    scope_matches_payload,
    staging_collection_name,
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
        staging_token_factory: Callable[[], str] | None = None,
        backend_schema_version: str = QDRANT_BACKEND_SCHEMA_VERSION,
    ):
        self._client = client
        self._prefix = str(collection_prefix or "ananta")
        self._clock = clock
        self._staging_token_factory = staging_token_factory or (
            lambda: uuid.uuid4().hex
        )
        backend_schema = str(backend_schema_version or "").strip()
        if (
            not backend_schema
            or len(backend_schema.encode("utf-8")) > 128
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in backend_schema
            )
        ):
            raise QdrantSchemaError("vector_store_invalid_schema_version")
        self._backend_schema_version = backend_schema

    @property
    def client(self) -> QdrantClientPort:
        return self._client

    @property
    def backend_schema_version(self) -> str:
        return self._backend_schema_version

    def alias_name(self, scope: VectorScope) -> str:
        return collection_alias(self._prefix, scope)

    def active_collection(self, scope: VectorScope) -> str | None:
        return self._client.resolve_alias(self.alias_name(scope))

    def target_collection_name(
        self,
        scope: VectorScope,
        *,
        index_version: str,
    ) -> str:
        """Derive the deterministic version target without reading or writing Qdrant."""

        return versioned_collection_name(self._prefix, scope, index_version)

    def create_versioned(
        self,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        *,
        index_version: str,
    ) -> str:
        collection_name = self.target_collection_name(
            scope,
            index_version=index_version,
        )
        info = self._client.collection_info(collection_name)
        if info is None:
            self._create_new_collection(collection_name, scope, compatibility)
            return collection_name
        if int(info.dimensions) != int(compatibility.dimensions):
            raise QdrantSchemaError("dimensions_mismatch")
        if str(info.distance).lower() != str(compatibility.distance).lower():
            raise QdrantSchemaError("distance_mismatch")
        report = self.compatibility(collection_name, compatibility)
        if not report.compatible:
            raise QdrantSchemaError(report.reason)
        return collection_name

    def create_staging(
        self,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        *,
        index_version: str,
    ) -> str:
        """Create a fresh collection that can never alias the active target."""

        active = self.active_collection(scope)
        for _ in range(8):
            collection_name = staging_collection_name(
                self._prefix,
                scope,
                index_version,
                self._staging_token_factory(),
            )
            if collection_name == active:
                continue
            if self._client.collection_info(collection_name) is not None:
                continue
            try:
                self._create_new_collection(collection_name, scope, compatibility)
            except QdrantClientError as exc:
                if exc.reason == "collection_exists":
                    continue
                raise
            return collection_name
        raise QdrantSchemaError("qdrant_staging_name_exhausted")

    def _create_new_collection(
        self,
        collection_name: str,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
    ) -> None:
        created = False
        try:
            self._client.create_collection(
                collection_name,
                dimensions=int(compatibility.dimensions),
                distance=str(compatibility.distance),
            )
            created = True
            self._client.upsert(
                collection_name,
                [
                    manifest_client_point(
                        collection_name,
                        scope,
                        compatibility,
                        created_at_epoch=self._clock(),
                        backend_schema_version=self._backend_schema_version,
                    )
                ],
            )
        except (QdrantClientError, QdrantSchemaError):
            if created:
                try:
                    if self.active_collection(scope) != collection_name:
                        self._client.delete_collection(collection_name)
                except QdrantClientError:
                    pass
            raise

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
        return self._compatibility_from_manifest(
            collection_name,
            expected,
            payload,
        )

    def _compatibility_from_manifest(
        self,
        collection_name: str,
        expected: CompatibilitySpec,
        payload: dict,
    ) -> CompatibilityReport:
        """Validate one already-read manifest against physical collection state."""

        found = dict(payload.get("compatibility") or {})
        found_backend_schema = str(
            payload.get("backend_schema_version") or ""
        ).strip()
        expected_payload = {
            **expected.as_dict(),
            "backend_schema_version": self._backend_schema_version,
        }
        found_payload = {
            **found,
            "backend_schema_version": found_backend_schema,
        }
        if found_backend_schema != self._backend_schema_version:
            return CompatibilityReport(
                False,
                "migration_required",
                expected_payload,
                found_payload,
            )
        info = self._client.collection_info(collection_name)
        if info is None:
            return CompatibilityReport(
                False,
                "migration_required",
                expected_payload,
                found_payload,
            )
        if int(info.dimensions) != int(expected.dimensions):
            return CompatibilityReport(
                False,
                "dimensions_mismatch",
                expected_payload,
                {**found_payload, "dimensions": int(info.dimensions)},
            )
        if str(info.distance).lower() != str(expected.distance).lower():
            return CompatibilityReport(
                False,
                "distance_mismatch",
                expected_payload,
                {**found_payload, "distance": str(info.distance).lower()},
            )
        report = compare_compatibility(expected, found)
        return CompatibilityReport(
            report.compatible,
            report.reason,
            expected_payload,
            found_payload,
        )

    def query_compatibility(
        self,
        collection_name: str,
        *,
        scope: VectorScope,
        expected: CompatibilitySpec | None = None,
        dimensions: int | None = None,
        distance: str | None = None,
    ) -> CompatibilityReport:
        """Compare an independent expected state with collection metadata."""

        payload = self._manifest_payload(collection_name)
        found = dict(payload.get("compatibility") or {})
        manifest_scope = dict(payload.get("scope") or {})
        if not scope_matches_payload(scope, manifest_scope):
            return CompatibilityReport(
                False,
                "vector_scope_conflict",
                {"scope": scope.as_dict()},
                {"scope": manifest_scope},
            )
        if expected is None:
            return CompatibilityReport(
                False,
                "vector_store_compatibility_required",
                {
                    "dimensions": int(dimensions or 0),
                    "distance": str(distance or ""),
                },
                found,
            )
        expected_payload = expected.as_dict()
        if missing_compatibility_fields(expected):
            return CompatibilityReport(
                False,
                "vector_store_compatibility_required",
                expected_payload,
                found,
            )
        if dimensions is not None and int(dimensions) != int(expected.dimensions):
            return CompatibilityReport(
                False,
                "dimensions_mismatch",
                expected_payload,
                {"dimensions": int(dimensions)},
            )
        if distance is not None and str(distance).lower() != str(expected.distance).lower():
            return CompatibilityReport(
                False,
                "distance_mismatch",
                expected_payload,
                {"distance": str(distance).lower()},
            )
        # Reuse the manifest already fetched for the scope check. Calling the
        # public compatibility method here used to retrieve the same immutable
        # manifest a second time for every search request.
        return self._compatibility_from_manifest(
            collection_name,
            expected,
            payload,
        )

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
        if missing_compatibility_fields(expected):
            raise QdrantSchemaError("rebuild_required")
        if not self.manifest_scope_matches(collection_name, scope):
            raise QdrantSchemaError("vector_scope_conflict")
        alias = self.alias_name(scope)
        if not str(collection_name).startswith(f"{alias}-"):
            raise QdrantSchemaError("vector_store_invalid_collection")
        report = self.compatibility(collection_name, expected)
        if not report.compatible:
            raise QdrantSchemaError(report.reason)
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

    def discard_staging(
        self,
        collection_name: str,
        *,
        scope: VectorScope | None = None,
    ) -> bool:
        try:
            manifest = self._manifest_payload(collection_name)
        except QdrantClientError:
            return False
        raw_scope = dict(manifest.get("scope") or {})
        if scope is None:
            try:
                scope = VectorScope(
                    workspace_id=str(raw_scope.get("workspace_id") or ""),
                    repository_id=str(raw_scope.get("repository_id") or ""),
                    profile_name=str(raw_scope.get("profile_name") or ""),
                    domain=str(raw_scope.get("domain") or ""),
                )
            except (TypeError, ValueError):
                return False
        if not scope_matches_payload(scope, raw_scope):
            return False
        alias = self.alias_name(scope)
        if not str(collection_name).startswith(f"{alias}-"):
            return False
        try:
            if self._client.resolve_alias(alias) == collection_name:
                return False
            self._client.delete_collection(collection_name)
        except QdrantClientError:
            # The active alias has not moved; cleanup can be retried by retention.
            return False
        return True
