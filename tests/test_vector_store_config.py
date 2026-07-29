from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.vector_store_config import (
    QdrantEndpointConfig,
    QdrantVectorStoreConfig,
    VectorStoreConfig,
    VectorStoreConfigError,
    VectorStoreProvider,
)


def test_json_is_secret_free_default() -> None:
    config = VectorStoreConfig.from_mapping({})
    assert config.provider == VectorStoreProvider.JSON
    assert config.qdrant is None
    assert len(config.config_hash()) == 24


def test_qdrant_local_requires_an_exact_allowlisted_origin() -> None:
    with pytest.raises(VectorStoreConfigError, match="vector_store_endpoint_not_allowlisted"):
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {
                    "url": "http://localhost:6333",
                    "allowed_origins": ["http://localhost:6334"],
                },
            }
        )


def test_qdrant_remote_requires_external_opt_in_and_rejects_url_credentials() -> None:
    with pytest.raises(VectorStoreConfigError, match="vector_store_external_calls_not_allowed"):
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {
                    "url": "https://qdrant.example.test",
                    "allowed_origins": ["https://qdrant.example.test"],
                },
            }
        )
    with pytest.raises(VectorStoreConfigError, match="userinfo_forbidden"):
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {
                    "url": "https://user:secret@qdrant.example.test",
                    "allowed_origins": ["https://qdrant.example.test"],
                    "external_calls_allowed": True,
                },
            }
        )


def test_qdrant_plaintext_secret_and_tls_disable_are_rejected() -> None:
    with pytest.raises(VectorStoreConfigError, match="plaintext_qdrant_api_key_forbidden"):
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {"api_key": "do-not-store"},
            }
        )
    with pytest.raises(VectorStoreConfigError, match="tls_verification_cannot_be_disabled"):
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {"tls_verify": False},
            }
        )


def test_qdrant_endpoint_has_separate_bounded_timeout_contract() -> None:
    endpoint = QdrantEndpointConfig()

    assert endpoint.connect_timeout_seconds == 3.0
    assert endpoint.request_timeout_seconds == 10.0
    assert endpoint.as_dict()["connect_timeout_seconds"] == 3.0
    assert endpoint.as_dict()["request_timeout_seconds"] == 10.0
    assert "timeout_seconds" not in endpoint.as_dict()

    parsed = QdrantEndpointConfig.from_mapping(
        {
            "timeout_seconds": 7,
            "connect_timeout_seconds": 2,
        }
    )
    assert parsed.connect_timeout_seconds == 2.0
    assert parsed.request_timeout_seconds == 7.0

    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantEndpointConfig.from_mapping(
            {
                "timeout_seconds": 7,
                "request_timeout_seconds": 8,
            }
        )
    assert exc.value.reason == "vector_store_invalid_timeout"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("external_calls_allowed", "false"),
        ("external_calls_allowed", 0),
        ("external_calls_allowed", 1),
        ("prefer_grpc", "false"),
        ("prefer_grpc", 0),
        ("prefer_grpc", 1),
        ("tls_verify", "false"),
        ("tls_verify", 0),
        ("tls_verify", 1),
    ],
)
def test_qdrant_endpoint_booleans_require_real_json_booleans(
    field: str,
    value: object,
) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantEndpointConfig.from_mapping({field: value})

    assert exc.value.reason == "vector_store_invalid_boolean"


@pytest.mark.parametrize(
    "field",
    ["connect_timeout_seconds", "request_timeout_seconds", "timeout_seconds"],
)
def test_qdrant_timeout_fields_reject_booleans(field: str) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantEndpointConfig.from_mapping({field: True})

    assert exc.value.reason == "vector_store_invalid_timeout"


@pytest.mark.parametrize(
    "value",
    ["false", 0, 1],
)
def test_embedding_text_opt_in_requires_a_real_json_boolean(value: object) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantVectorStoreConfig.from_mapping({"store_embedding_text": value})

    assert exc.value.reason == "vector_store_invalid_boolean"


@pytest.mark.parametrize("value", [True, False])
def test_retention_count_rejects_boolean_as_integer(value: bool) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantVectorStoreConfig.from_mapping({"retention_collections": value})

    assert exc.value.reason == "vector_store_invalid_collection"
    assert exc.value.cause_reason == "invalid_qdrant_retention_collections"


@pytest.mark.parametrize(
    "value",
    ["", "contains spaces", "x" * 129, 1],
)
def test_backend_schema_version_is_a_bounded_identifier(value: object) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantVectorStoreConfig.from_mapping({"schema_version": value})

    assert exc.value.reason == "vector_store_invalid_collection"
    assert exc.value.cause_reason == "invalid_qdrant_schema_version"


@pytest.mark.parametrize(
    "payload",
    [
        {"allowed_origins": []},
        {"allowed_origins": "http://localhost:6333"},
        {"allowed_origins": 1},
        {"allowed_origins": [""]},
        {"allowed_base_urls": []},
        {"trusted_private_origins": "http://qdrant:6333"},
    ],
)
def test_origin_lists_are_typed_and_explicit_empty_never_uses_defaults(
    payload: dict[str, object],
) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        QdrantEndpointConfig.from_mapping(payload)

    assert exc.value.reason == "vector_store_invalid_origin"


def test_config_hash_excludes_secret_reference_values() -> None:
    first = VectorStoreConfig.from_mapping(
        {
            "provider": "qdrant",
            "qdrant": {
                "rest_url": "https://localhost:6333",
                "allowed_origins": ["https://localhost:6333"],
                "api_key_ref": "env://ANANTA_QDRANT_API_KEY",
                "tls_ca_cert_ref": (
                    "secretfile:///run/secrets/qdrant-tls-ca-a.pem"
                ),
            },
        }
    )
    second = VectorStoreConfig.from_mapping(
        {
            "provider": "qdrant",
            "qdrant": {
                "rest_url": "https://localhost:6333",
                "allowed_origins": ["https://localhost:6333"],
                "api_key_ref": "env://ANANTA_QDRANT_API_KEY_ROTATED",
                "tls_ca_cert_ref": (
                    "secretfile:///run/secrets/qdrant-tls-ca-b.pem"
                ),
            },
        }
    )

    assert first.config_hash() == second.config_hash()
    assert first.as_dict()["qdrant"]["endpoint"]["api_key_ref"]
    assert first.as_dict()["qdrant"]["endpoint"]["tls_ca_cert_ref"]


@pytest.mark.parametrize(
    "legacy_field",
    ["fail_mode", "fallback_provider"],
)
def test_top_level_legacy_availability_fields_are_rejected(
    legacy_field: str,
) -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        VectorStoreConfig.from_mapping(
            {
                "availability": {
                    "on_unavailable": "degraded_empty",
                },
                legacy_field: (
                    "degraded_empty"
                    if legacy_field == "fail_mode"
                    else "json"
                ),
            }
        )

    assert exc.value.reason == "vector_store_invalid_availability_policy"
    assert (
        exc.value.cause_reason
        == "legacy_availability_fields_not_supported"
    )


def test_remote_plaintext_transport_has_stable_tls_policy_code() -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {
                    "url": "http://qdrant.example.test:6333",
                    "allowed_origins": ["http://qdrant.example.test:6333"],
                    "external_calls_allowed": True,
                },
            }
        )

    assert exc.value.reason == "vector_store_tls_policy_violation"


def test_exact_trusted_private_origin_requires_internal_tls_transport() -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {
                    "url": "http://qdrant:6333",
                    "allowed_origins": ["http://qdrant:6333"],
                    "trusted_private_origins": ["http://qdrant:6333"],
                    "external_calls_allowed": False,
                },
            }
        )

    assert exc.value.reason == "vector_store_tls_policy_violation"
    assert exc.value.cause_reason == "vector_store_remote_rest_tls_required"


def test_exact_trusted_private_origin_allows_verified_internal_tls() -> None:
    config = VectorStoreConfig.from_mapping(
        {
            "provider": "qdrant",
            "qdrant": {
                "url": "https://qdrant:6333",
                "allowed_origins": ["https://qdrant:6333"],
                "trusted_private_origins": ["https://qdrant:6333"],
                "tls_ca_cert_ref": (
                    "secretfile:///run/secrets/qdrant-tls-ca.pem"
                ),
                "external_calls_allowed": False,
            },
        }
    )

    endpoint = config.qdrant.endpoint
    assert endpoint.rest_url == "https://qdrant:6333"
    assert endpoint.trusted_private_origins == ("https://qdrant:6333",)
    assert endpoint.tls_ca_cert_ref.endswith("/qdrant-tls-ca.pem")


def test_tls_ca_reference_is_file_only_and_requires_tls() -> None:
    for payload in (
        {
            "rest_url": "https://qdrant:6333",
            "allowed_origins": ["https://qdrant:6333"],
            "trusted_private_origins": ["https://qdrant:6333"],
            "tls_ca_cert_ref": "env://ANANTA_QDRANT_TLS_CA",
        },
        {
            "rest_url": "http://localhost:6333",
            "allowed_origins": ["http://localhost:6333"],
            "tls_ca_cert_ref": "secretfile:///run/secrets/qdrant-tls-ca.pem",
        },
    ):
        with pytest.raises(
            VectorStoreConfigError,
            match="vector_store_tls_policy_violation",
        ):
            QdrantEndpointConfig.from_mapping(payload)


def test_trusted_private_origin_cannot_expand_the_allowlist() -> None:
    with pytest.raises(VectorStoreConfigError) as exc:
        VectorStoreConfig.from_mapping(
            {
                "provider": "qdrant",
                "qdrant": {
                    "url": "http://localhost:6333",
                    "allowed_origins": ["http://localhost:6333"],
                    "trusted_private_origins": ["http://qdrant:6333"],
                },
            }
        )

    assert exc.value.reason == "vector_store_invalid_origin"
    assert (
        exc.value.cause_reason
        == "vector_store_trusted_private_origin_not_allowlisted"
    )


@pytest.mark.parametrize(
    "example_name",
    [
        "vector-store.json-local.json",
        "vector-store.qdrant-local.json",
        "vector-store.qdrant-remote.json",
    ],
)
def test_vector_store_examples_are_loadable(example_name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "examples" / example_name).read_text(encoding="utf-8"))
    config = VectorStoreConfig.from_mapping(payload)
    assert config.config_hash()
    if example_name == "vector-store.qdrant-remote.json":
        assert config.provider == VectorStoreProvider.JSON
        assert config.qdrant is not None
