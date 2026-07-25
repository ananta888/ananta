from __future__ import annotations

import pytest

from agent.adapters.vector_store_metrics_adapter import (
    PrometheusVectorStoreObserver,
    VectorStoreMetricInstruments,
)
from worker.retrieval.vector_store_observer import VectorStoreOperationObservation


class _Metric:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def labels(self, *labels):
        self.calls.append(("labels", labels))
        return self

    def inc(self, amount=1):
        self.calls.append(("inc", amount))

    def observe(self, value):
        self.calls.append(("observe", value))


def _observer():
    metrics = [_Metric() for _ in range(4)]
    observer = PrometheusVectorStoreObserver(VectorStoreMetricInstruments(*metrics))
    return observer, metrics


def test_vector_store_observer_emits_only_bounded_labels_and_numeric_counts() -> None:
    observer, metrics = _observer()
    observer.observe(
        VectorStoreOperationObservation(
            backend="qdrant",
            operation="search",
            outcome="success",
            duration_seconds=0.25,
            counts={"hits": 4},
        )
    )

    assert metrics[0].calls[0] == (
        "labels",
        ("qdrant", "search", "success", "ok"),
    )
    assert metrics[1].calls == [
        ("labels", ("qdrant", "search", "success")),
        ("observe", 0.25),
    ]
    assert metrics[2].calls == [
        ("labels", ("qdrant", "search", "success", "hits")),
        ("inc", 4),
    ]
    assert metrics[3].calls == []


def test_vector_store_fallback_observation_has_explicit_requested_and_effective_backend() -> None:
    observer, metrics = _observer()
    observer.observe(
        VectorStoreOperationObservation(
            backend="json",
            operation="search",
            outcome="degraded",
            reason_code="fallback_state_incompatible",
            requested_backend="qdrant",
            effective_backend="json",
            provider_fallback=True,
        )
    )

    assert metrics[3].calls == [
        (
            "labels",
            ("qdrant", "json", "fallback_state_incompatible"),
        ),
        ("inc", 1),
    ]


def test_vector_store_observation_rejects_unbounded_dimensions() -> None:
    with pytest.raises(ValueError, match="backend_invalid"):
        VectorStoreOperationObservation(
            backend="tenant-a",
            operation="search",
            outcome="success",
        )
    with pytest.raises(ValueError, match="count_key_invalid"):
        VectorStoreOperationObservation(
            backend="qdrant",
            operation="search",
            outcome="success",
            counts={"workspace-a": 1},
        )


def test_unknown_reason_is_collapsed_to_other() -> None:
    observation = VectorStoreOperationObservation(
        backend="qdrant",
        operation="health",
        outcome="failed",
        reason_code="https://user:secret@example.invalid/private",
    )

    assert observation.reason_code == "other"
