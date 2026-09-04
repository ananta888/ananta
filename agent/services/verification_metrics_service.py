"""Small deterministic aggregation of verification pilot outcomes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


class VerificationMetricsService:
    @staticmethod
    def summarize(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        values = [dict(item) for item in reports]
        statuses = Counter(str(item.get("status") or "unknown") for item in values)
        durations = [int(item.get("duration_ms") or 0) for item in values]
        return {
            "schema": "ananta.verification-metrics.v1",
            "runs": len(values),
            "by_status": dict(sorted(statuses.items())),
            "counterexamples": sum(len(list(item.get("counterexamples") or [])) for item in values),
            "duration_ms_total": sum(durations),
            "duration_ms_max": max(durations, default=0),
        }


__all__ = ["VerificationMetricsService"]
