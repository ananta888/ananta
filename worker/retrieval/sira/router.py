from __future__ import annotations

import time
from dataclasses import dataclass

from worker.retrieval.sira.config import SiraConfig, SiraMode
from worker.retrieval.sira.contracts import RoutingDecision
from worker.retrieval.sira.query_expander import classify_exact_query


@dataclass(slots=True)
class SiraCircuitBreaker:
    failure_threshold: int = 3
    recovery_seconds: float = 30.0
    consecutive_failures: int = 0
    opened_at: float = 0.0

    def allow(self, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        if self.consecutive_failures < self.failure_threshold:
            return True
        if current - self.opened_at >= self.recovery_seconds:
            self.consecutive_failures = 0
            self.opened_at = 0.0
            return True
        return False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = 0.0

    def record_failure(self, *, now: float | None = None) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold and self.opened_at <= 0.0:
            self.opened_at = time.monotonic() if now is None else float(now)


class SiraRouter:
    def __init__(self, *, config: SiraConfig, circuit_breaker: SiraCircuitBreaker | None = None):
        self._config = config
        self._circuit_breaker = circuit_breaker or SiraCircuitBreaker()

    @property
    def circuit_breaker(self) -> SiraCircuitBreaker:
        return self._circuit_breaker

    def decide(
        self,
        *,
        query: str,
        corpus_ready: bool,
        baseline_margin: float | None = None,
        expansion_cached: bool = False,
        model_budget_available: bool = True,
    ) -> RoutingDecision:
        features = {
            "mode": self._config.mode.value,
            "corpus_ready": bool(corpus_ready),
            "baseline_margin": baseline_margin,
            "expansion_cached": bool(expansion_cached),
            "model_budget_available": bool(model_budget_available),
            "exact_query_kind": classify_exact_query(query) or "",
        }
        required = self._config.mode == SiraMode.REQUIRED
        if self._config.mode == SiraMode.OFF:
            return RoutingDecision(False, False, False, "mode_off", features=features)
        if not corpus_ready:
            return RoutingDecision(False, False, required, "corpus_unavailable", features=features)
        if not self._circuit_breaker.allow():
            return RoutingDecision(False, False, required, "circuit_open", features=features)
        if classify_exact_query(query):
            return RoutingDecision(
                True,
                self._config.mode == SiraMode.SHADOW,
                required,
                "exact_query_original_only",
                features=features,
            )
        if not model_budget_available and not expansion_cached:
            return RoutingDecision(False, False, required, "model_budget_unavailable", features=features)
        if (
            baseline_margin is not None
            and baseline_margin >= self._config.minimum_baseline_margin
            and not expansion_cached
        ):
            return RoutingDecision(False, False, False, "baseline_high_confidence", features=features)
        if self._config.mode == SiraMode.ON_DEMAND and baseline_margin is None and not expansion_cached:
            return RoutingDecision(False, False, False, "on_demand_not_requested", features=features)
        return RoutingDecision(
            True,
            self._config.mode == SiraMode.SHADOW,
            required,
            "sira_selected",
            features=features,
        )
