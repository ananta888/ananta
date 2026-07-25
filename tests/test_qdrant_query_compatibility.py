from __future__ import annotations

from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import manifest_client_point
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope


class _ManifestClient:
    def __init__(self, manifest: object) -> None:
        self._manifest = manifest

    def retrieve(self, collection_name: str, point_ids: object) -> tuple[object, ...]:
        del collection_name, point_ids
        return (self._manifest,)


def test_query_compatibility_reuses_non_queryable_manifest_fields() -> None:
    scope = VectorScope("workspace", "repository", "runtime-profile")
    stored = CompatibilitySpec(
        dimensions=3,
        distance="cosine",
        provider="embedding-provider",
        model="embedding-model",
        profile="embedding-profile",
        encoding="float32",
        config_hash="config",
        schema_version="stored-schema.v1",
        manifest_hash="manifest",
    )
    collection = "ananta-codecompass-version"
    manager = QdrantCollectionManager(
        _ManifestClient(
            manifest_client_point(
                collection,
                scope,
                stored,
                created_at_epoch=1.0,
            )
        )
    )

    compatible = manager.query_compatibility(
        collection,
        scope=scope,
        dimensions=3,
        distance="cosine",
    )
    dimensions_mismatch = manager.query_compatibility(
        collection,
        scope=scope,
        dimensions=4,
        distance="cosine",
    )
    scope_mismatch = manager.query_compatibility(
        collection,
        scope=VectorScope("other", "repository", "runtime-profile"),
        dimensions=3,
        distance="cosine",
    )

    assert compatible.compatible is True
    assert dimensions_mismatch.reason == "dimensions_mismatch"
    assert scope_mismatch.reason == "vector_scope_conflict"
