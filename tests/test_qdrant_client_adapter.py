from __future__ import annotations

from types import SimpleNamespace

import pytest

from worker.retrieval import qdrant_client_port
from worker.retrieval.qdrant_client_port import (
    FilterCondition,
    QdrantClientAdapter,
    QdrantClientError,
    QdrantExtraRequiredError,
    ServerFilter,
)


class _Model:
    def __init__(self, **values):
        self.__dict__.update(values)


class _Models:
    MatchAny = _Model
    MatchValue = _Model
    FieldCondition = _Model
    Filter = _Model


class _RawClient:
    def __init__(self):
        self.query_calls = []

    def query_points(self, **values):
        self.query_calls.append(values)
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="point-1",
                    score=0.75,
                    payload={"record_id": "record-1"},
                )
            ]
        )

    def search(self, **values):
        raise AssertionError(f"legacy search API must not be called: {values}")


def test_adapter_uses_qdrant_118_query_points_not_legacy_search() -> None:
    raw = _RawClient()
    adapter = QdrantClientAdapter(
        rest_origin="http://localhost:6333",
        raw_client=raw,
        models_module=_Models,
    )
    query_filter = ServerFilter(
        (
            FilterCondition("workspace_id", ("workspace",)),
            FilterCondition("repository_id", ("repository",)),
        )
    )

    result = adapter.query_points(
        "collection",
        query_vector=(1.0, 0.0),
        query_filter=query_filter,
        limit=5,
    )

    assert len(raw.query_calls) == 1
    assert raw.query_calls[0]["query"] == [1.0, 0.0]
    assert raw.query_calls[0]["limit"] == 5
    assert result[0].payload["record_id"] == "record-1"


def test_missing_optional_dependency_has_stable_install_reason(monkeypatch) -> None:
    def missing(_name):
        raise ModuleNotFoundError("qdrant-client unavailable")

    monkeypatch.setattr(qdrant_client_port.importlib, "import_module", missing)

    with pytest.raises(QdrantExtraRequiredError) as exc:
        QdrantClientAdapter(rest_origin="http://localhost:6333")

    assert exc.value.reason == "qdrant_extra_required"


def test_foreign_exception_text_is_not_exposed() -> None:
    class _FailingClient(_RawClient):
        def query_points(self, **values):
            raise RuntimeError("Authorization: top-secret api_key=top-secret")

    adapter = QdrantClientAdapter(
        rest_origin="http://localhost:6333",
        raw_client=_FailingClient(),
        models_module=_Models,
    )
    query_filter = ServerFilter((FilterCondition("workspace_id", ("workspace",)),))

    with pytest.raises(QdrantClientError) as exc:
        adapter.query_points(
            "collection",
            query_vector=(1.0,),
            query_filter=query_filter,
            limit=1,
        )

    assert exc.value.reason == "qdrant_unavailable"
    assert "top-secret" not in str(exc.value)
