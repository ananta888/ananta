"""Endpoint-scoped availability circuit for Hub-owned management dispatch."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RestrictedInferenceCircuitDecision:
    allowed: bool
    state: str
    retry_after_seconds: float = 0.0


@dataclass
class _EndpointCircuitState:
    opened_until: float
    probe_in_flight: bool = False


class RestrictedInferenceManagementCircuitBreaker:
    """Coalesce repeated transport failures without making policy decisions.

    The first proven availability failure opens only the affected endpoint.
    Once the bounded cooldown expires, exactly one Hub request becomes the
    half-open probe. Every logical management task still reaches its own
    deterministic failed/retryable persistence and audit path.
    """

    def __init__(
        self,
        *,
        open_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if open_seconds <= 0 or open_seconds > 3_600:
            raise ValueError("restricted inference circuit cooldown is invalid")
        self._open_seconds = float(open_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, _EndpointCircuitState] = {}

    def before_request(self, endpoint_key: str) -> RestrictedInferenceCircuitDecision:
        key = self._require_key(endpoint_key)
        now = self._clock()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return RestrictedInferenceCircuitDecision(allowed=True, state="closed")
            if state.opened_until > now:
                return RestrictedInferenceCircuitDecision(
                    allowed=False,
                    state="open",
                    retry_after_seconds=max(0.0, state.opened_until - now),
                )
            if state.probe_in_flight:
                return RestrictedInferenceCircuitDecision(
                    allowed=False,
                    state="half_open",
                )
            state.probe_in_flight = True
            return RestrictedInferenceCircuitDecision(allowed=True, state="half_open")

    def record_unavailable(self, endpoint_key: str) -> None:
        key = self._require_key(endpoint_key)
        with self._lock:
            self._states[key] = _EndpointCircuitState(
                opened_until=self._clock() + self._open_seconds,
            )

    def record_reachable(self, endpoint_key: str) -> None:
        key = self._require_key(endpoint_key)
        with self._lock:
            self._states.pop(key, None)

    @staticmethod
    def _require_key(endpoint_key: str) -> str:
        key = str(endpoint_key or "").strip()
        if not key:
            raise ValueError("restricted inference circuit endpoint key is required")
        return key


def _configured_open_seconds() -> float:
    raw = str(
        os.getenv("ANANTA_RESTRICTED_INFERENCE_CIRCUIT_OPEN_SECONDS", "60")
    ).strip()
    try:
        return float(raw)
    except ValueError:
        return 60.0


_shared_circuit_breaker = RestrictedInferenceManagementCircuitBreaker(
    open_seconds=_configured_open_seconds()
)


def get_restricted_inference_management_circuit_breaker(
) -> RestrictedInferenceManagementCircuitBreaker:
    return _shared_circuit_breaker


__all__ = [
    "RestrictedInferenceCircuitDecision",
    "RestrictedInferenceManagementCircuitBreaker",
    "get_restricted_inference_management_circuit_breaker",
]
