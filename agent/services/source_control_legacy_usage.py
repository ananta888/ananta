"""Bounded usage projection for deprecated source-control aliases."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock


class BoundedLegacySourceControlUsage:
    """Count fixed route labels only; never retain user or object dimensions."""

    def __init__(self, *, max_labels: int = 32) -> None:
        if max_labels < 1 or max_labels > 128:
            raise ValueError("legacy_usage_limit_invalid")
        self._max_labels = max_labels
        self._counts: OrderedDict[str, int] = OrderedDict()
        self._lock = Lock()

    def record(self, label: str) -> None:
        normalized = str(label or "")[:64]
        if not normalized:
            return
        with self._lock:
            if (
                normalized not in self._counts
                and len(self._counts) >= self._max_labels
            ):
                normalized = "other"
            self._counts[normalized] = min(
                self._counts.get(normalized, 0) + 1,
                2_147_483_647,
            )

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


__all__ = ["BoundedLegacySourceControlUsage"]
