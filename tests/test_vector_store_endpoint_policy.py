from __future__ import annotations

import pytest

from worker.retrieval.vector_store_endpoint_policy import (
    EnvFileSecretResolver,
    SecretReference,
    VectorStoreEndpointPolicyError,
    VectorStoreSecretError,
    normalize_endpoint,
    normalize_trusted_private_origins,
    validate_endpoint_access,
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


def test_trusted_private_origin_is_exact_and_must_be_allowlisted() -> None:
    endpoint = validate_endpoint_access(
        "https://qdrant:6333",
        transport="rest",
        allowed_origins=("https://qdrant:6333",),
        trusted_private_origins=("https://qdrant:6333",),
        external_calls_allowed=False,
    )

    assert endpoint.origin == "https://qdrant:6333"
    with pytest.raises(
        VectorStoreEndpointPolicyError,
        match="remote_rest_tls_required",
    ):
        validate_endpoint_access(
            "http://qdrant:6333",
            transport="rest",
            allowed_origins=("http://qdrant:6333",),
            trusted_private_origins=("http://qdrant:6333",),
            external_calls_allowed=False,
        )
    with pytest.raises(
        VectorStoreEndpointPolicyError,
        match="trusted_private_origin_not_allowlisted",
    ):
        normalize_trusted_private_origins(
            ("https://qdrant:6333",),
            allowed_origins=("http://localhost:6333",),
        )


def test_secret_reference_accepts_only_env_and_absolute_secretfile() -> None:
    assert SecretReference.parse("env://ANANTA_QDRANT_API_KEY").locator == "ANANTA_QDRANT_API_KEY"
    assert (
        SecretReference.parse("secretfile:///run/secrets/qdrant-api-key").as_uri()
        == "secretfile:///run/secrets/qdrant-api-key"
    )
    for value in (
        "secret",
        "env://bad-name",
        "file:///run/secrets/qdrant-api-key",
        "file://relative",
        "https://example.test/key",
    ):
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
    assert (
        resolver.resolve(
            SecretReference.parse(f"secretfile://{secret_file}")
        )
        == "secret-value"
    )

    outside = tmp_path / "outside"
    outside.write_text("forbidden", encoding="utf-8")
    with pytest.raises(VectorStoreSecretError, match="path_not_allowed"):
        resolver.resolve(
            SecretReference.parse(f"secretfile://{outside}")
        )

    with pytest.raises(VectorStoreSecretError, match="env_not_allowed"):
        resolver.resolve(SecretReference.parse("env://UNRELATED_PROCESS_SECRET"))
