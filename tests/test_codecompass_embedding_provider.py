from __future__ import annotations

import pytest

from worker.retrieval.embedding_provider import (
    EmbeddingProviderUnavailable,
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    build_embedding_provider,
)


def test_embedding_provider_factory_supports_fake_and_local_provider_modes():
    fake = build_embedding_provider({"provider": "fake", "dimensions": 6, "model_version": "fake-test-v1"})
    local = build_embedding_provider({"provider": "local", "dimensions": 6, "model_version": "hash-local-v1"})

    fake_vectors = fake.embed_texts(["payment timeout", "payment timeout"])
    local_vectors = local.embed_texts(["payment timeout", "payment timeout"])

    assert len(fake_vectors) == 2
    assert len(fake_vectors[0]) == 6
    assert fake_vectors[0] == fake_vectors[1]
    assert len(local_vectors[0]) == 6
    assert local_vectors[0] == local_vectors[1]


def test_openai_compatible_provider_requires_runtime_configuration():
    provider = OpenAICompatibleEmbeddingProvider(base_url="", api_key=None)
    with pytest.raises(EmbeddingProviderUnavailable):
        provider.embed_texts(["hello"])


def test_hash_provider_remains_backward_compatible():
    provider = HashEmbeddingProvider(dimensions=4)
    vectors = provider.embed_texts(["x", "y"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 4
    assert vectors[0] != vectors[1]

