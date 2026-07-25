"""Prometheus adapter for bounded vector-store operation observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.retrieval.vector_store_observer import VectorStoreOperationObservation


@dataclass(frozen=True)
class VectorStoreMetricInstruments:
    operations_total: Any
    duration_seconds: Any
    items_total: Any
    fallbacks_total: Any


def default_vector_store_metric_instruments() -> VectorStoreMetricInstruments:
    from agent.metrics import (
        VECTOR_STORE_FALLBACKS_TOTAL,
        VECTOR_STORE_ITEMS_TOTAL,
        VECTOR_STORE_OPERATION_DURATION_SECONDS,
        VECTOR_STORE_OPERATIONS_TOTAL,
    )

    return VectorStoreMetricInstruments(
        operations_total=VECTOR_STORE_OPERATIONS_TOTAL,
        duration_seconds=VECTOR_STORE_OPERATION_DURATION_SECONDS,
        items_total=VECTOR_STORE_ITEMS_TOTAL,
        fallbacks_total=VECTOR_STORE_FALLBACKS_TOTAL,
    )


class PrometheusVectorStoreObserver:
    """Translate a safe domain observation into bounded metric series."""

    def __init__(
        self,
        instruments: VectorStoreMetricInstruments | None = None,
    ) -> None:
        self._instruments = instruments or default_vector_store_metric_instruments()

    def observe(self, observation: VectorStoreOperationObservation) -> None:
        self._instruments.operations_total.labels(
            observation.backend,
            observation.operation,
            observation.outcome,
            observation.reason_code,
        ).inc()
        self._instruments.duration_seconds.labels(
            observation.backend,
            observation.operation,
            observation.outcome,
        ).observe(observation.duration_seconds)
        for count_kind, value in observation.counts.items():
            if value:
                self._instruments.items_total.labels(
                    observation.backend,
                    observation.operation,
                    observation.outcome,
                    count_kind,
                ).inc(value)
        if observation.provider_fallback:
            self._instruments.fallbacks_total.labels(
                observation.requested_backend,
                observation.effective_backend,
                observation.reason_code,
            ).inc()


__all__ = [
    "PrometheusVectorStoreObserver",
    "VectorStoreMetricInstruments",
    "default_vector_store_metric_instruments",
]
