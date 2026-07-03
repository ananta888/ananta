from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ProviderLabel(str, Enum):
    CODECOMPASS = "codecompass"
    AUGMENT_MCP = "augment_mcp"
    AUGGIE_CLI = "auggie_cli"
    AUGGIE_INTERACTIVE = "auggie_interactive"
    FAKE = "fake"

@dataclass
class MetricEvent:
    event_id: str
    provider: ProviderLabel
    operation: str          # "retrieve" | "worker_run" | "session" | "benchmark"
    latency_ms: int
    cost_units: float       # abstract unit: 0.0 for local, > 0 for external
    items_returned: int
    items_blocked: int
    error: bool
    timestamp: float
    run_id: str | None = None
    # No snippet content, no paths that could be secrets

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "provider": self.provider.value,
            "operation": self.operation, "latency_ms": self.latency_ms,
            "cost_units": self.cost_units, "items_returned": self.items_returned,
            "items_blocked": self.items_blocked, "error": self.error,
            "timestamp": self.timestamp, "run_id": self.run_id,
        }

@dataclass
class ProviderMetricsSummary:
    provider: str
    total_operations: int
    total_latency_ms: int
    total_cost_units: float
    total_items_returned: int
    total_items_blocked: int
    error_count: int
    avg_latency_ms: float
    avg_items_per_call: float
    error_rate: float
    period_start: float
    period_end: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "total_operations": self.total_operations,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "total_cost_units": round(self.total_cost_units, 6),
            "avg_items_per_call": round(self.avg_items_per_call, 2),
            "error_rate": round(self.error_rate, 3),
            "period_start": self.period_start,
            "period_end": self.period_end,
        }

@dataclass
class ProviderComparison:
    query_used: str
    providers: dict[str, ProviderMetricsSummary]
    fastest_provider: str | None
    cheapest_provider: str | None
    most_results_provider: str | None
    created_at: float

    def to_markdown(self) -> str:
        lines = [
            f"# Provider Comparison", "",
            "| Provider | Ops | Avg Latency | Cost | Items/call | Error Rate |",
            "|---|---|---|---|---|---|",
        ]
        for name, s in self.providers.items():
            lines.append(
                f"| {name} | {s.total_operations} | {s.avg_latency_ms:.0f}ms | "
                f"{s.total_cost_units:.4f} | {s.avg_items_per_call:.1f} | {s.error_rate:.1%} |"
            )
        if self.fastest_provider:
            lines += ["", f"**Fastest:** {self.fastest_provider}"]
        if self.cheapest_provider:
            lines += [f"**Cheapest:** {self.cheapest_provider}"]
        return "\n".join(lines)

class AugmentMetricsCollector:
    """
    AUG-905: Provider-scoped metrics collection.
    No secrets, no content, no paths stored.
    """

    def __init__(self) -> None:
        self._events: list[MetricEvent] = []

    def record(self, *, provider: ProviderLabel, operation: str,
               latency_ms: int, cost_units: float = 0.0,
               items_returned: int = 0, items_blocked: int = 0,
               error: bool = False, run_id: str | None = None) -> MetricEvent:
        ev = MetricEvent(
            event_id=str(uuid.uuid4())[:8],
            provider=provider, operation=operation,
            latency_ms=max(0, latency_ms), cost_units=max(0.0, cost_units),
            items_returned=max(0, items_returned), items_blocked=max(0, items_blocked),
            error=error, timestamp=time.time(), run_id=run_id,
        )
        self._events.append(ev)
        return ev

    def summary_for(self, provider: ProviderLabel, *,
                    since: float | None = None) -> ProviderMetricsSummary | None:
        events = [e for e in self._events if e.provider == provider]
        if since:
            events = [e for e in events if e.timestamp >= since]
        if not events:
            return None
        total_ops = len(events)
        total_lat = sum(e.latency_ms for e in events)
        total_cost = sum(e.cost_units for e in events)
        total_items = sum(e.items_returned for e in events)
        total_blocked = sum(e.items_blocked for e in events)
        errors = sum(1 for e in events if e.error)
        return ProviderMetricsSummary(
            provider=provider.value,
            total_operations=total_ops,
            total_latency_ms=total_lat,
            total_cost_units=total_cost,
            total_items_returned=total_items,
            total_items_blocked=total_blocked,
            error_count=errors,
            avg_latency_ms=total_lat / total_ops,
            avg_items_per_call=total_items / total_ops,
            error_rate=errors / total_ops,
            period_start=min(e.timestamp for e in events),
            period_end=max(e.timestamp for e in events),
        )

    def compare_providers(self, query_used: str = "") -> ProviderComparison:
        summaries: dict[str, ProviderMetricsSummary] = {}
        for provider in ProviderLabel:
            s = self.summary_for(provider)
            if s:
                summaries[provider.value] = s

        fastest = min(summaries, key=lambda k: summaries[k].avg_latency_ms) if summaries else None
        cheapest = min(summaries, key=lambda k: summaries[k].total_cost_units) if summaries else None
        most_results = max(summaries, key=lambda k: summaries[k].avg_items_per_call) if summaries else None

        return ProviderComparison(
            query_used=query_used, providers=summaries,
            fastest_provider=fastest, cheapest_provider=cheapest,
            most_results_provider=most_results, created_at=time.time(),
        )

    def all_events(self) -> list[MetricEvent]:
        return list(self._events)

    def reset(self) -> None:
        self._events.clear()
