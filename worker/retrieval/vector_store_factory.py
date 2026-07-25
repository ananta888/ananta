from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import Any

from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_config import (
    VectorStoreConfig,
    VectorStoreConfigError,
    VectorStoreProvider,
)
from worker.retrieval.vector_store_contract import VectorStore
from worker.retrieval.vector_store_endpoint_policy import SecretResolver


VectorStoreBuilder = Callable[..., VectorStore]


class VectorStoreFactory:
    """Composition-root registry; backend modules remain isolated and lazy."""

    def __init__(self, builders: Mapping[str, VectorStoreBuilder] | None = None) -> None:
        self._builders: dict[str, VectorStoreBuilder] = dict(builders or {})

    def register(
        self,
        provider: str | VectorStoreProvider,
        builder: VectorStoreBuilder,
        *,
        replace: bool = False,
    ) -> None:
        key = provider.value if isinstance(provider, VectorStoreProvider) else str(provider).strip().lower()
        if not key:
            raise VectorStoreConfigError("missing_vector_store_provider")
        if key in self._builders and not replace:
            raise VectorStoreConfigError(f"vector_store_provider_already_registered:{key}")
        self._builders[key] = builder

    def create(
        self,
        config: VectorStoreConfig,
        *,
        secret_resolver: SecretResolver | None = None,
    ) -> VectorStore:
        provider = config.provider.value
        builder = self._builders.get(provider)
        if builder is not None:
            return builder(config, secret_resolver=secret_resolver)
        if config.provider == VectorStoreProvider.JSON:
            return JsonVectorStore(index_path=config.json.index_path)
        if config.provider == VectorStoreProvider.QDRANT:
            if config.qdrant is None:
                raise VectorStoreConfigError("missing_qdrant_vector_store_config")
            try:
                module = importlib.import_module("worker.retrieval.qdrant_vector_store")
                backend_type: Any = getattr(module, "QdrantVectorStore")
            except (ImportError, AttributeError) as exc:
                raise VectorStoreConfigError(
                    "qdrant_backend_not_installed: install the ananta[qdrant] extra"
                ) from exc
            return backend_type.from_config(config.qdrant, secret_resolver=secret_resolver)
        raise VectorStoreConfigError(f"unknown_vector_store_provider:{provider}")


def build_vector_store(
    config: VectorStoreConfig | Mapping[str, Any] | None = None,
    *,
    factory: VectorStoreFactory | None = None,
    secret_resolver: SecretResolver | None = None,
) -> VectorStore:
    resolved = config if isinstance(config, VectorStoreConfig) else VectorStoreConfig.from_mapping(config)
    return (factory or VectorStoreFactory()).create(resolved, secret_resolver=secret_resolver)


__all__ = ["VectorStoreBuilder", "VectorStoreFactory", "build_vector_store"]
