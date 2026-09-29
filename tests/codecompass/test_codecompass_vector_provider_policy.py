from __future__ import annotations

import pytest

from agent.services.embedding_provider_config_service import (
    EmbeddingProviderConfig,
    build_embedding_provider_from_config,
)
from worker.retrieval.embedding_provider import OpenAICompatibleEmbeddingProvider


def test_openai_compatible_local_allowlist_is_forwarded_to_builder() -> None:
    cfg = EmbeddingProviderConfig(
        provider="openai_compatible",
        model="nomic-embed-text",
        model_version="nomic-embed-text-v1",
        dimensions=768,
        base_url="http://localhost:11434/v1",
        external_calls_allowed=True,
        allowed_base_urls=["http://localhost:11434"],
        scope="codecompass_vector",
    )

    provider = build_embedding_provider_from_config(cfg)

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.base_url == "http://localhost:11434/v1"


def test_openai_compatible_without_external_allow_remains_blocked() -> None:
    cfg = EmbeddingProviderConfig(
        provider="openai_compatible",
        base_url="http://localhost:11434/v1",
        external_calls_allowed=False,
        allowed_base_urls=["http://localhost:11434"],
        scope="codecompass_vector",
    )

    with pytest.raises(ValueError, match="embedding_provider_blocked"):
        build_embedding_provider_from_config(cfg)


def test_openai_compatible_non_allowlisted_base_url_remains_blocked() -> None:
    cfg = EmbeddingProviderConfig(
        provider="openai_compatible",
        base_url="http://example.com/v1",
        external_calls_allowed=True,
        allowed_base_urls=["http://localhost:11434"],
        scope="codecompass_vector",
    )

    with pytest.raises(ValueError, match="embedding_provider_blocked"):
        build_embedding_provider_from_config(cfg)


def test_openai_compatible_provider_posts_embeddings_payload(monkeypatch) -> None:
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"embedding":[0.1,0.2,0.3]}]}'

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = req.data.decode("utf-8")
        return _Response()

    monkeypatch.setattr("worker.retrieval.embedding_provider.request.urlopen", _fake_urlopen)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://localhost:1234/v1",
        api_key="test-key",
        model="nomic-embed-text",
        model_version="nomic-embed-text-v1",
        dimensions=3,
    )

    vectors = provider.embed_texts(["hello vector"])

    assert captured["url"] == "http://localhost:1234/v1/embeddings"
    assert '"model": "nomic-embed-text"' in captured["body"]
    assert '"hello vector"' in captured["body"]
    assert vectors == [[0.1, 0.2, 0.3]]


def test_vector_engine_bad_provider_config_degrades_without_hash_fallback(tmp_path) -> None:
    from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
    from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore

    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    engine = CodeCompassVectorEngine.build_from_config(
        store,
        provider_config={
            "provider": "openai_compatible",
            "base_url": "http://example.com/v1",
            "external_calls_allowed": False,
        },
    )

    assert engine.search(query="payment") == []
    assert engine.last_diagnostic()["status"] == "degraded"
    assert engine.last_diagnostic()["reason"] == "provider_resolution_failed"
