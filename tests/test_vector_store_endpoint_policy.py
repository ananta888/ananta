from __future__ import annotations

import pytest

from worker.retrieval.vector_store_endpoint_policy import (
    EnvFileSecretResolver,
    SecretReference,
    VectorStoreEndpointPolicyError,
    VectorStoreSecretError,
    normalize_endpoint,
)


def test_endpoint_normalization_rejects_query_fragment_path_and_userinfo() -> None:
    assert normalize_endpoint("HTTP://LOCALHOST:6333/").origin == "http://localhost:6333"
    for value in (
        "http://localhost:6333/collections",
        "http://localhost:6333?key=value",
        "http://localhost:6333#fragment",
        "http://user:secret@localhost:6333",
    ):
        with pytest.raises(VectorStoreEndpointPolicyError):
            normalize_endpoint(value)


def test_secret_reference_accepts_only_env_and_absolute_file() -> None:
    assert SecretReference.parse("env://ANANTA_QDRANT_API_KEY").locator == "ANANTA_QDRANT_API_KEY"
    assert SecretReference.parse("file:///run/secrets/qdrant-api-key").locator == "/run/secrets/qdrant-api-key"
    for value in ("secret", "env://bad-name", "file://relative", "https://example.test/key"):
        with pytest.raises(VectorStoreEndpointPolicyError):
            SecretReference.parse(value)


def test_injected_secret_resolver_is_bounded_to_allowed_root(tmp_path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    secret_file = root / "qdrant"
    secret_file.write_text("secret-value\n", encoding="utf-8")
    resolver = EnvFileSecretResolver(
        environ={"ANANTA_QDRANT_API_KEY": "env-secret"},
        allowed_file_roots=(root,),
    )

    assert resolver.resolve(SecretReference.parse("env://ANANTA_QDRANT_API_KEY")) == "env-secret"
    assert resolver.resolve(SecretReference.parse(f"file://{secret_file}")) == "secret-value"

    outside = tmp_path / "outside"
    outside.write_text("forbidden", encoding="utf-8")
    with pytest.raises(VectorStoreSecretError, match="path_not_allowed"):
        resolver.resolve(SecretReference.parse(f"file://{outside}"))
