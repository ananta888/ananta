"""Bounded, content-safe operational telemetry for DSPy optimization."""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter, deque
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ananta_contracts.dspy_optimization import canonical_json, require_id

_SECRET = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._~+/-]+|api[_-]?key\s*[=:]\s*\S+)")


class DspyOperationalTelemetry:
    def __init__(self, *, max_events: int = 2_000) -> None:
        if not 100 <= max_events <= 100_000:
            raise ValueError("dspy_telemetry_capacity_invalid")
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, str]] = Counter()
        self._measurements: dict[str, dict[str, float]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)

    def record_job(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        action: str,
        run_id: str,
        revision: int,
        reason_code: str,
        correlation_id: str,
        target_digest: str,
    ) -> None:
        event = {
            "schema": "ananta.dspy-audit-event.v1",
            "recorded_at": _now(),
            "actor_id": require_id(actor_id, "audit_actor_id"),
            "tenant_id": require_id(tenant_id, "tenant_id"),
            "action": require_id(action, "audit_action"),
            "target_digest": target_digest,
            "revision": revision,
            "reason_code": require_id(reason_code, "reason_code"),
            "correlation_id": require_id(correlation_id, "correlation_id"),
            "run_digest": hashlib.sha256(run_id.encode()).hexdigest(),
        }
        self._record("jobs", action, event)

    def record_worker(self, event: Mapping[str, Any]) -> None:
        allowed = {
            "schema",
            "run_id",
            "attempt_id",
            "binding_id",
            "role",
            "request_digest",
            "input_digest",
            "output_digest",
            "input_bytes",
            "output_bytes",
            "usage",
            "finish_reason",
            "cache_hit",
            "retry_count",
            "latency_ms",
            "rollout_id",
            "cost_micros",
            "observed_provider_cost_micros",
        }
        if set(event) - allowed:
            raise ValueError("dspy_telemetry_event_invalid")
        safe = dict(event)
        safe["run_digest"] = hashlib.sha256(str(safe.pop("run_id", "")).encode()).hexdigest()
        safe["attempt_digest"] = hashlib.sha256(str(safe.pop("attempt_id", "")).encode()).hexdigest()
        self._record("provider_calls", "completed", safe)
        self.observe_metric("latency_ms", safe.get("latency_ms", 0))
        self.observe_metric("tokens", dict(safe.get("usage") or {}).get("total_tokens", 0))
        self.observe_metric("cost_micros", safe.get("cost_micros", 0))
        self.observe_metric("retries", safe.get("retry_count", 0))
        self.record_outcome(kind="cache", outcome="hit" if safe.get("cache_hit") is True else "miss_or_unknown")

    def record_outcome(self, *, kind: str, outcome: str) -> None:
        if kind not in {"evaluations", "promotions", "rollbacks", "budget_stops", "retries", "cache"}:
            raise ValueError("dspy_telemetry_kind_invalid")
        self._record(kind, require_id(outcome, "telemetry_outcome"), None)

    def projection(self) -> dict[str, Any]:
        with self._lock:
            counters = {f"{kind}:{outcome}": count for (kind, outcome), count in sorted(self._counters.items())}
            events = [dict(value) for value in self._events]
            measurements = {key: dict(value) for key, value in sorted(self._measurements.items())}
        return {
            "schema": "ananta.dspy-operational-telemetry.v1",
            "counters": counters,
            "recent_events": events,
            "measurements": measurements,
            "prometheus_label_policy": ["kind", "outcome"],
            "content_labels_forbidden": ["tenant_id", "run_id", "prompt", "output"],
        }

    def observe_metric(self, name: str, value: object) -> None:
        if name not in {
            "duration_ms",
            "queue_time_ms",
            "latency_ms",
            "tokens",
            "cost_micros",
            "retries",
            "evaluation_scores",
        }:
            raise ValueError("dspy_telemetry_metric_invalid")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("dspy_telemetry_metric_value_invalid")
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError("dspy_telemetry_metric_value_invalid")
        with self._lock:
            aggregate = self._measurements.setdefault(name, {"count": 0, "sum": 0, "max": 0})
            aggregate["count"] += 1
            aggregate["sum"] += number
            aggregate["max"] = max(aggregate["max"], number)

    def _record(self, kind: str, outcome: str, event: Mapping[str, Any] | None) -> None:
        with self._lock:
            self._counters[(kind, outcome)] += 1
            if event is not None:
                rendered = canonical_json(event)
                if _SECRET.search(rendered):
                    raise ValueError("dspy_telemetry_secret_detected")
                self._events.append(dict(event))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["DspyOperationalTelemetry"]
