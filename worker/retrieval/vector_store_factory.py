from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import Any

from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_config import (
    AvailabilityMode,
    VectorStoreConfig,
    VectorStoreConfigError,
    VectorStoreProvider,
)
from worker.retrieval.vector_store_contract import VectorStore
from worker.retrieval.vector_store_endpoint_policy import SecretResolver
from worker.retrieval.vector_store_observer import VectorStoreObserver

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
        observer: VectorStoreObserver | None = None,
    ) -> VectorStore:
        provider = config.provider.value
        builder = self._builders.get(provider)
        if builder is not None:
            builder_kwargs: dict[str, Any] = {
                "secret_resolver": secret_resolver,
            }
            if observer is not None:
                builder_kwargs["observer"] = observer
            store = builder(config, **builder_kwargs)
            return self._with_availability_policy(
                store,
                config=config,
                observer=observer,
            )
        if config.provider == VectorStoreProvider.JSON:
            return JsonVectorStore(index_path=config.json.index_path)
        if config.provider == VectorStoreProvider.DUCKDB:
            if config.duckdb is None:
                raise VectorStoreConfigError("missing_duckdb_vector_store_config")
            try:
                module = importlib.import_module("worker.retrieval.duckdb_vector_store")
                backend_type: Any = getattr(module, "DuckDBVectorStore")
            except (ImportError, AttributeError) as exc:
                raise VectorStoreConfigError(
                    "duckdb_backend_not_installed: install the ananta[duckdb] extra"
                ) from exc
            store = backend_type.from_config(config.duckdb)
            return self._with_availability_policy(
                store,
                config=config,
                observer=observer,
            )
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
            store = backend_type.from_config(
                config.qdrant,
                secret_resolver=secret_resolver,
                observer=observer,
            )
            return self._with_availability_policy(
                store,
                config=config,
                observer=observer,
            )
        raise VectorStoreConfigError(f"unknown_vector_store_provider:{provider}")

    @staticmethod
    def _with_availability_policy(
        store: VectorStore,
        *,
        config: VectorStoreConfig,
        observer: VectorStoreObserver | None = None,
    ) -> VectorStore:
        if config.provider not in {VectorStoreProvider.QDRANT, VectorStoreProvider.DUCKDB}:
            return store
        from worker.retrieval.vector_store_fallback import (
            AvailabilityManagedVectorStore,
            ClientAvailabilityProbe,
            FallbackVectorSearch,
        )

        fallback: JsonVectorStore | None = None
        if config.availability.on_unavailable == AvailabilityMode.EXPLICIT_JSON_FALLBACK:
            fallback = JsonVectorStore(index_path=config.json.index_path)

        def fallback_is_compatible(query: Any) -> bool:
            compatibility = getattr(query, "compatibility", None)
            if fallback is None or compatibility is None:
                return False
            try:
                return fallback.compatibility_reason(compatibility) == "unchanged"
            except (OSError, TypeError, ValueError):
                return False

        client = getattr(store, "_client", None)
        probe = ClientAvailabilityProbe(client) if client is not None else None
        search = FallbackVectorSearch(
            primary=store,
            fallback=fallback,
            policy=config.availability,
            availability_probe=probe,
            fallback_compatibility=fallback_is_compatible,
            observer=observer,
        )
        return AvailabilityManagedVectorStore(
            primary=store,
            search=search,
            fallback=fallback,
        )


def build_vector_store(
    config: VectorStoreConfig | Mapping[str, Any] | None = None,
    *,
    factory: VectorStoreFactory | None = None,
    secret_resolver: SecretResolver | None = None,
    observer: VectorStoreObserver | None = None,
) -> VectorStore:
    resolved = config if isinstance(config, VectorStoreConfig) else VectorStoreConfig.from_mapping(config)
    return (factory or VectorStoreFactory()).create(
        resolved,
        secret_resolver=secret_resolver,
        observer=observer,
    )


__all__ = ["VectorStoreBuilder", "VectorStoreFactory", "build_vector_store"]
