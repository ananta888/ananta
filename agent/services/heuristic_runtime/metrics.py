"""Heuristic Runtime Metriken — UI-Latenz, LLM-Latenz, Jitter, Rollback-Count."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SnakeRuntimeMetrics:
    ui_decision_latency_ms: float = 0.0
    llm_background_latency_ms: float = 0.0
    jitter_score: float = 0.0
    target_switch_rate: float = 0.0
    no_action_rate: float = 0.0
    rollback_count: int = 0
    _samples: list[float] = field(default_factory=list, repr=False)

    def record_ui_decision(self, latency_ms: float) -> None:
        self.ui_decision_latency_ms = latency_ms
        self._samples.append(latency_ms)
        if len(self._samples) > 100:
            self._samples.pop(0)

    def record_llm_background(self, latency_ms: float) -> None:
        self.llm_background_latency_ms = latency_ms

    def record_rollback(self) -> None:
        self.rollback_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ui_decision_latency_ms": round(self.ui_decision_latency_ms, 2),
            "llm_background_latency_ms": round(self.llm_background_latency_ms, 2),
            "jitter_score": round(self.jitter_score, 3),
            "target_switch_rate": round(self.target_switch_rate, 3),
            "no_action_rate": round(self.no_action_rate, 3),
            "rollback_count": self.rollback_count,
        }
