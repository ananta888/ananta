"""Least-privilege, bounded local LiveKit/runtime probe."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen


class LiveKitProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LiveKitProbeConfig:
    health_url: str = "http://127.0.0.1:7880/"
    metrics_url: str = "http://127.0.0.1:7880/metrics"
    timeout_seconds: float = 2.0
    response_bytes_max: int = 262_144

    def __post_init__(self) -> None:
        if self.health_url != "http://127.0.0.1:7880/" or self.metrics_url != "http://127.0.0.1:7880/metrics":
            raise ValueError("runtime_probe_source_must_be_fixed_loopback")
        if not 0.1 <= self.timeout_seconds <= 5 or not 4_096 <= self.response_bytes_max <= 1_048_576:
            raise ValueError("runtime_probe_bound_invalid")


class LiveKitProbe:
    """Returns aggregate allowlisted values; labeled samples are ignored."""

    _METRICS = {
        "livekit_room_total": "rooms",
        "livekit_participant_total": "participants",
        "livekit_node_packet_out_bytes": "egress_bytes",
        "process_resident_memory_bytes": "memory_bytes",
        "process_open_fds": "fd_count",
        "process_cpu_seconds_total": "cpu_seconds_total",
    }
    _SAMPLE = re.compile(
        r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)\s+(?P<value>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$"
    )

    def __init__(
        self,
        config: LiveKitProbeConfig | None = None,
        *,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self._config = config or LiveKitProbeConfig()
        self._opener = opener

    def collect(self) -> dict[str, float | int | bool | None]:
        health = self._read(self._config.health_url, accept="text/plain")
        metrics = self._read(self._config.metrics_url, accept="text/plain")
        values: dict[str, float | int | bool | None] = {
            target: None for target in self._METRICS.values()
        }
        for raw in metrics.decode("ascii").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "{" in line or "}" in line:
                continue
            match = self._SAMPLE.fullmatch(line)
            if match is None or match.group("name") not in self._METRICS:
                continue
            value = float(match.group("value"))
            if not math.isfinite(value) or value < 0:
                raise LiveKitProbeError("runtime_probe_metric_invalid")
            values[self._METRICS[match.group("name")]] = int(value) if value.is_integer() else value
        values["liveness"] = bool(health)
        return values

    def _read(self, url: str, *, accept: str) -> bytes:
        try:
            with self._opener(
                Request(url, headers={"Accept": accept}, method="GET"),
                timeout=self._config.timeout_seconds,
            ) as response:
                payload = response.read(self._config.response_bytes_max + 1)
        except Exception as exc:  # noqa: BLE001
            raise LiveKitProbeError("runtime_probe_source_unavailable") from exc
        if len(payload) > self._config.response_bytes_max:
            raise LiveKitProbeError("runtime_probe_response_oversize")
        return payload


__all__ = ["LiveKitProbe", "LiveKitProbeConfig", "LiveKitProbeError"]

