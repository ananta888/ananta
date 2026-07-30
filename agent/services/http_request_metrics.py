"""Fixed-cardinality in-process metrics for successful read-only HTTP traffic."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import threading
from typing import Protocol


class HttpRequestMetricsPort(Protocol):
    def observe(
        self,
        *,
        method: str,
        status_code: int,
        duration_ms: int,
    ) -> None: ...


@dataclass(frozen=True)
class HttpRequestMetric:
    method: str
    status_family: str
    count: int
    total_duration_ms: int
    max_duration_ms: int


class BoundedHttpRequestMetrics(HttpRequestMetricsPort):
    """Aggregate into a finite method/status matrix without route or ID labels."""

    _METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST"})
    _STATUS_FAMILIES = frozenset({"1xx", "2xx", "3xx", "4xx", "5xx", "other"})
    _MAX_DURATION_MS = 3_600_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._duration_totals: dict[tuple[str, str], int] = defaultdict(int)
        self._duration_maxima: dict[tuple[str, str], int] = defaultdict(int)

    @classmethod
    def _key(cls, *, method: str, status_code: int) -> tuple[str, str]:
        normalized_method = str(method or "").upper()
        if normalized_method not in cls._METHODS:
            normalized_method = "OTHER"
        normalized_status = int(status_code)
        status_family = (
            f"{normalized_status // 100}xx"
            if 100 <= normalized_status <= 599
            else "other"
        )
        if status_family not in cls._STATUS_FAMILIES:
            status_family = "other"
        return normalized_method, status_family

    def observe(
        self,
        *,
        method: str,
        status_code: int,
        duration_ms: int,
    ) -> None:
        key = self._key(method=method, status_code=status_code)
        bounded_duration = max(0, min(int(duration_ms), self._MAX_DURATION_MS))
        with self._lock:
            self._counts[key] += 1
            self._duration_totals[key] += bounded_duration
            self._duration_maxima[key] = max(
                self._duration_maxima[key],
                bounded_duration,
            )

    def snapshot(self) -> tuple[HttpRequestMetric, ...]:
        with self._lock:
            return tuple(
                HttpRequestMetric(
                    method=method,
                    status_family=status_family,
                    count=self._counts[(method, status_family)],
                    total_duration_ms=self._duration_totals[(method, status_family)],
                    max_duration_ms=self._duration_maxima[(method, status_family)],
                )
                for method, status_family in sorted(self._counts)
            )


_metrics = BoundedHttpRequestMetrics()


def get_http_request_metrics() -> HttpRequestMetricsPort:
    return _metrics


__all__ = [
    "BoundedHttpRequestMetrics",
    "HttpRequestMetric",
    "HttpRequestMetricsPort",
    "get_http_request_metrics",
]
