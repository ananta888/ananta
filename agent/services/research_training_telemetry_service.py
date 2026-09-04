"""Hub-owned validation and delivery of normalized research metrics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.research_training_ports import ResearchTelemetryPort
from ananta_contracts.research_training_metrics import ResearchMetricEventV1


class ResearchTrainingTelemetryService:
    def __init__(self, sinks: Sequence[ResearchTelemetryPort] = ()) -> None:
        self._sinks = tuple(sinks)
        self._last_sequence: dict[tuple[str, str, str, str], int] = {}
        self._events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def ingest(self, event: Mapping[str, Any]) -> dict[str, Any]:
        parsed = ResearchMetricEventV1.from_mapping(event)
        key = (parsed.tenant_id, parsed.run_id, parsed.stage_id, parsed.attempt_id)
        last = self._last_sequence.get(key, -1)
        if parsed.sequence <= last:
            raise ValueError("research_metric_sequence_stale")
        projection = parsed.to_dict()
        for sink in self._sinks:
            sink.record(event=projection)
        run_key = (parsed.tenant_id, parsed.run_id)
        events = self._events.setdefault(run_key, [])
        if len(events) >= 10_000:
            raise ValueError("research_metric_run_limit_exceeded")
        events.append(projection)
        self._last_sequence[key] = parsed.sequence
        return {
            "schema": "ananta.research-training-metric-receipt.v1",
            "event_digest": parsed.digest,
            "sinks": len(self._sinks),
            "offline": not self._sinks,
            "human_intervention_required": False,
        }

    def list_run(self, *, tenant_id: str, run_id: str, limit: int = 500) -> dict[str, Any]:
        if not 1 <= limit <= 1000:
            raise ValueError("research_metric_list_limit_invalid")
        items = self._events.get((str(tenant_id), str(run_id)), [])[-limit:]
        return {
            "schema": "ananta.research-training-metric-list.v1",
            "items": [dict(item) for item in items],
            "limit": limit,
        }


class InMemoryResearchTelemetrySink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, *, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


__all__ = ["InMemoryResearchTelemetrySink", "ResearchTrainingTelemetryService"]
