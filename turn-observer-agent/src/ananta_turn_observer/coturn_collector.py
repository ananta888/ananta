"""Bounded, content-free collector for coturn's local Prometheus endpoint."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen


class CoturnCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CoturnCollectorConfig:
    source_url: str = "http://127.0.0.1:9641/metrics"
    timeout_seconds: float = 2.0
    max_response_bytes: int = 262_144

    def __post_init__(self) -> None:
        if self.source_url != "http://127.0.0.1:9641/metrics":
            raise ValueError("coturn_metrics_source_must_be_loopback_fixed")
        if not 0.1 <= self.timeout_seconds <= 5.0:
            raise ValueError("coturn_metrics_timeout_out_of_bounds")
        if not 1_024 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("coturn_metrics_response_limit_out_of_bounds")


class CoturnAggregateCollector:
    """Reads only an explicit allowlist of unlabeled aggregate metrics."""

    METRICS = {
        "turn_total_allocations": "allocations_total",
        "turn_current_allocations": "allocations_active",
        "turn_total_traffic_rcvp": "bytes_received_total",
        "turn_total_traffic_sent": "bytes_sent_total",
        "turn_total_traffic_rcvb": "packets_received_total",
        "turn_total_traffic_sentb": "packets_sent_total",
    }
    _SAMPLE = re.compile(
        r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\s+(?P<value>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$"
    )

    def __init__(
        self,
        config: CoturnCollectorConfig,
        *,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self._config = config
        self._opener = opener

    def collect(self) -> dict[str, int | None]:
        request = Request(
            self._config.source_url,
            headers={"Accept": "text/plain", "User-Agent": "ananta-turn-observer/0.1"},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self._config.timeout_seconds) as response:
                payload = response.read(self._config.max_response_bytes + 1)
        except Exception as exc:  # noqa: BLE001 - normalized into a non-secret reason.
            raise CoturnCollectionError("coturn_metrics_unavailable") from exc
        if len(payload) > self._config.max_response_bytes:
            raise CoturnCollectionError("coturn_metrics_response_too_large")
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as exc:
            raise CoturnCollectionError("coturn_metrics_non_ascii") from exc
        result: dict[str, int | None] = {target: None for target in self.METRICS.values()}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "{" in line or "}" in line:
                continue
            match = self._SAMPLE.fullmatch(line)
            if match is None:
                continue
            target = self.METRICS.get(match.group("name"))
            if target is None:
                continue
            value = float(match.group("value"))
            if not math.isfinite(value) or value < 0 or not value.is_integer():
                raise CoturnCollectionError("coturn_metric_value_invalid")
            result[target] = int(value)
        return result

