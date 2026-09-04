"""Bounded-cardinality, content-free collaboration runtime signals."""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any


class CollaborationOperationalSignals:
    SIGNALS = frozenset(
        {
            "api",
            "bridge_reconnect",
            "command",
            "event_admission",
            "loop_detection",
            "projection",
            "replay",
            "revocation",
            "search",
            "security",
        }
    )
    OUTCOMES = frozenset({"success", "error", "blocked"})
    LATENCY_BUCKETS_MS = (10, 50, 100, 250, 500, 1_000, 2_500, 5_000)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, str]] = Counter()
        self._latency: Counter[tuple[str, int]] = Counter()

    def record(self, signal: str, *, outcome: str, duration_ms: float = 0.0) -> None:
        if signal not in self.SIGNALS or outcome not in self.OUTCOMES:
            raise ValueError("collaboration_operational_signal_invalid")
        if not isinstance(duration_ms, (int, float)) or isinstance(duration_ms, bool) or duration_ms < 0:
            raise ValueError("collaboration_operational_duration_invalid")
        bucket = next((limit for limit in self.LATENCY_BUCKETS_MS if duration_ms <= limit), 10_000)
        with self._lock:
            self._counts[(signal, outcome)] += 1
            self._latency[(signal, bucket)] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = dict(self._counts)
            latency = dict(self._latency)
        return {
            "operations": sum(counts.values()),
            "errors": sum(count for (_signal, outcome), count in counts.items() if outcome == "error"),
            "blocked": sum(count for (_signal, outcome), count in counts.items() if outcome == "blocked"),
            "replays": sum(count for (signal, _outcome), count in counts.items() if signal == "replay"),
            "revocation_errors": counts.get(("revocation", "error"), 0),
            "reconnect_errors": counts.get(("bridge_reconnect", "error"), 0),
            "loop_detections": sum(count for (signal, _outcome), count in counts.items() if signal == "loop_detection"),
            "slow_operations": sum(count for (_signal, bucket), count in latency.items() if bucket >= 1_000),
            "series": len(counts) + len(latency),
            "maximum_series": len(self.SIGNALS) * (len(self.OUTCOMES) + len(self.LATENCY_BUCKETS_MS) + 1),
            "content_included": False,
        }


__all__ = ["CollaborationOperationalSignals"]
