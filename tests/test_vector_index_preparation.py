from __future__ import annotations

import pytest

from worker.retrieval.embedding_text_builder import (
    CODECOMPASS_EMBEDDING_TEXT_PROFILE,
)
from worker.retrieval.vector_index_preparation import (
    CODECOMPASS_DOCUMENTS,
    VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
    VECTOR_INDEX_PREPARATION_SCHEMA,
    WIKI_DOCUMENTS,
    VectorIndexPreparationService,
    VectorIndexPreparationSpec,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    VectorScope,
)
from worker.retrieval.wiki_vector_store import (
    WIKI_EMBEDDING_PROFILE,
)


def _scope(domain: str = "codecompass") -> VectorScope:
    return VectorScope(
        workspace_id="workspace-a",
        repository_id="repository-a",
        profile_name="default",
        domain=domain,
    )


def _embedding() -> dict:
    return {
        "provider": "local_hash",
        "model_version": "hash-v1",
        "dimensions": 12,
        "timeout_seconds": 20,
        "external_calls_allowed": False,
        "allowed_base_urls": [],
    }


def _compatibility(
    *,
    profile: str = CODECOMPASS_EMBEDDING_TEXT_PROFILE,
    schema_version: str = "codecompass_vector_index.v2",
) -> CompatibilitySpec:
    return CompatibilitySpec(
        dimensions=12,
        distance="cosine",
        provider="local_hash",
        model="hash-v1",
        profile=profile,
        encoding="float32",
        config_hash="config-a",
        schema_version=schema_version,
        manifest_hash="manifest-a",
    )


def _preparation(kind: str, profile: str) -> dict:
    return {
        "schema": VECTOR_INDEX_PREPARATION_SCHEMA,
        "kind": kind,
        "embedding": _embedding(),
        "embedding_text_profile": profile,
    }


def test_worker_prepares_codecompass_vectors_from_document_input() -> None:
    service = VectorIndexPreparationService()

    points = service.prepare(
        document_input={
            "schema": VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
            "kind": CODECOMPASS_DOCUMENTS,
            "documents": (
                {
                    "record_id": "record-a",
                    "kind": "python_function",
                    "file": "src/a.py",
                    "parent_id": "",
                    "role_labels": ["service"],
                    "importance_score": 0.8,
                    "source_scope": "repo",
                    "profile_name": "default",
                    "manifest_hash": "manifest-a",
                    "embedding_text": "payment timeout",
                    "source_hash": "source-a",
                },
            ),
        },
        scope=_scope(),
        compatibility=_compatibility(),
        preparation=_preparation(
            CODECOMPASS_DOCUMENTS,
            CODECOMPASS_EMBEDDING_TEXT_PROFILE,
        ),
    )

    assert len(points) == 1
    assert len(points[0].vector) == 12
    assert points[0].scope == _scope()
    assert points[0].source_hash == "source-a"
    assert points[0].payload["file"] == "src/a.py"


def test_worker_prepares_wiki_vectors_with_separate_adapter() -> None:
    service = VectorIndexPreparationService()
    compatibility = _compatibility(
        profile=WIKI_EMBEDDING_PROFILE,
        schema_version="ananta.wiki_vector_payload.v1",
    )

    points = service.prepare(
        document_input={
            "schema": VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
            "kind": WIKI_DOCUMENTS,
            "documents": (
                {
                    "record_id": "wiki-a",
                    "embedding_text": "bounded wiki section",
                    "kind": "wiki_section_chunk",
                    "file": "wiki/a",
                    "source_scope": "wiki",
                },
            ),
        },
        scope=_scope("wiki"),
        compatibility=compatibility,
        preparation={
            **_preparation(
                WIKI_DOCUMENTS,
                WIKI_EMBEDDING_PROFILE,
            ),
            "retrieval_cache_state": "wiki-cache-a",
        },
    )

    assert len(points) == 1
    assert points[0].scope.domain == "wiki"
    assert points[0].payload["kind"] == "wiki_section_chunk"
    assert points[0].payload["source_scope"] == "wiki"


def test_preparation_rejects_external_provider_without_https_allowlist() -> None:
    with pytest.raises(
        ValueError,
        match="vector_index_preparation_embedding_policy_invalid",
    ):
        VectorIndexPreparationSpec.from_mapping(
            {
                "schema": VECTOR_INDEX_PREPARATION_SCHEMA,
                "kind": CODECOMPASS_DOCUMENTS,
                "embedding": {
                    "provider": "openai_compatible",
                    "model": "embedding-a",
                    "model_version": "embedding-a",
                    "dimensions": 12,
                    "base_url": "http://embedding.example/v1",
                    "api_key_ref": "env://ANANTA_EMBEDDING_API_KEY",
                    "external_calls_allowed": True,
                    "allowed_base_urls": ["http://embedding.example/v1"],
                },
                "embedding_text_profile": (CODECOMPASS_EMBEDDING_TEXT_PROFILE),
            }
        )


def test_preparation_fails_closed_on_compatibility_mismatch() -> None:
    service = VectorIndexPreparationService()
    incompatible = CompatibilitySpec(
        **{
            **_compatibility().as_dict(),
            "model": "different-model",
        }
    )

    with pytest.raises(ValueError, match="model_changed"):
        service.prepare(
            document_input={
                "schema": VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
                "kind": CODECOMPASS_DOCUMENTS,
                "documents": (
                    {
                        "record_id": "record-a",
                        "embedding_text": "payment timeout",
                        "source_hash": "source-a",
                    },
                ),
            },
            scope=_scope(),
            compatibility=incompatible,
            preparation=_preparation(
                CODECOMPASS_DOCUMENTS,
                CODECOMPASS_EMBEDDING_TEXT_PROFILE,
            ),
        )
