from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.vector_store_config import (
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


@pytest.mark.parametrize(
    "example_name",
    ["vector-store.json-local.json", "vector-store.qdrant-local.json"],
)
def test_vector_store_examples_are_loadable(example_name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "examples" / example_name).read_text(encoding="utf-8"))
    config = VectorStoreConfig.from_mapping(payload)
    assert config.config_hash()
