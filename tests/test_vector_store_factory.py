from __future__ import annotations

import pytest

from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_config import VectorStoreConfig, VectorStoreConfigError
from worker.retrieval.vector_store_factory import VectorStoreFactory


def test_factory_keeps_json_as_default(tmp_path) -> None:
    config = VectorStoreConfig.for_json(tmp_path / "index.json")
    store = VectorStoreFactory().create(config)

    assert isinstance(store, JsonVectorStore)
    assert store.index_path == tmp_path / "index.json"
    assert store.diagnostics().provider == "json"


def test_config_rejects_unknown_provider_before_factory() -> None:
    with pytest.raises(VectorStoreConfigError, match="unknown_vector_store_provider"):
        VectorStoreConfig.from_mapping({"provider": "unknown"})


def test_factory_registry_is_explicit_and_non_overwriting(tmp_path) -> None:
    sentinel = JsonVectorStore(index_path=tmp_path / "sentinel.json")
    factory = VectorStoreFactory({"json": lambda _config, **_kwargs: sentinel})

    assert factory.create(VectorStoreConfig.for_json(tmp_path / "ignored.json")) is sentinel
    with pytest.raises(VectorStoreConfigError, match="already_registered"):
        factory.register("json", lambda _config, **_kwargs: sentinel)
