"""DecisionTrace — immutable record of a single heuristic decision cycle.

Based on build_prediction_trace() pattern from pipeline_trace.py.
No sensitive raw data is stored: text fields are hashed or truncated.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionTrace:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    surface: str = ""
    context_hash: str = ""
    lease_id: str | None = None
    heuristic_id: str | None = None
    strategy_id: str | None = None
    rule_id: str | None = None
    confidence: float = 0.0
    fallback_reason: str | None = None
    source: str = "heuristic"  # ai | heuristic | hybrid
    action_kind: str = "no_action"
    started_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    reason_codes: list[str] = field(default_factory=list)
    # v2: snapshot/semantic references (best-effort, blockiert keine UI-Entscheidung)
    snapshot_hash: str | None = None
    delta_hash: str | None = None
    semantic_hash: str | None = None
    heuristic_experiment_id: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.resolved_at is None:
            return None
        return max(0.0, (self.resolved_at - self.started_at) * 1000)

    def resolve(self, *, resolved_at: float | None = None) -> "DecisionTrace":
        self.resolved_at = resolved_at or time.time()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "surface": self.surface,
            "context_hash": self.context_hash,
            "lease_id": self.lease_id,
            "heuristic_id": self.heuristic_id,
            "strategy_id": self.strategy_id,
            "rule_id": self.rule_id,
            "confidence": self.confidence,
            "fallback_reason": self.fallback_reason,
            "source": self.source,
            "action_kind": self.action_kind,
            "started_at": self.started_at,
            "resolved_at": self.resolved_at,
            "duration_ms": self.duration_ms,
            "reason_codes": list(self.reason_codes),
            # v2 fields
            "snapshot_hash": self.snapshot_hash,
            "delta_hash": self.delta_hash,
            "semantic_hash": self.semantic_hash,
            "heuristic_experiment_id": self.heuristic_experiment_id,
        }

    @staticmethod
    def from_decision_result(
        result: Any,
        *,
        surface: str,
        context_hash: str,
        lease_id: str | None = None,
    ) -> "DecisionTrace":
        from agent.services.heuristic_runtime.decision_result import DecisionResult
        r: DecisionResult = result
        return DecisionTrace(
            surface=surface,
            context_hash=context_hash,
            lease_id=lease_id,
            heuristic_id=getattr(r, "strategy_id", None),
            strategy_id=getattr(r, "strategy_id", None),
            rule_id=getattr(r, "rule_id", None),
            confidence=r.confidence,
            fallback_reason=r.fallback_reason,
            source=r.source,
            action_kind=r.action_kind,
            reason_codes=list(r.reason_codes),
        )


# ── Metrics ───────────────────────────────────────────────────────────────────

@dataclass
class DomainMetrics:
    surface: str
    ai_success: int = 0
    ai_timeout: int = 0
    invalid_ai: int = 0
    heuristic_fallback: int = 0
    ttl_expired: int = 0
    reevaluation_count: int = 0
    no_match: int = 0
    user_override: int = 0
    total: int = 0

    def record(self, trace: DecisionTrace) -> None:
        self.total += 1
        if trace.source == "ai":
            self.ai_success += 1
        elif trace.fallback_reason == "ai_timeout":
            self.ai_timeout += 1
        elif trace.fallback_reason == "invalid_response":
            self.invalid_ai += 1
        elif trace.fallback_reason == "lease_expired":
            self.ttl_expired += 1
        elif trace.source == "heuristic":
            self.heuristic_fallback += 1

        if trace.action_kind == "no_action" and trace.confidence == 0.0:
            self.no_match += 1
        if "reevaluation" in trace.reason_codes:
            self.reevaluation_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "total": self.total,
            "ai_success": self.ai_success,
            "ai_timeout": self.ai_timeout,
            "invalid_ai": self.invalid_ai,
            "heuristic_fallback": self.heuristic_fallback,
            "ttl_expired": self.ttl_expired,
            "reevaluation_count": self.reevaluation_count,
            "no_match": self.no_match,
            "user_override": self.user_override,
        }


class DecisionMetricsAccumulator:
    """Per-domain metrics accumulator (in-memory, not persisted)."""

    def __init__(self) -> None:
        self._metrics: dict[str, DomainMetrics] = {}

    def record(self, trace: DecisionTrace) -> None:
        surface = trace.surface
        if surface not in self._metrics:
            self._metrics[surface] = DomainMetrics(surface=surface)
        self._metrics[surface].record(trace)

    def get(self, surface: str) -> DomainMetrics:
        if surface not in self._metrics:
            self._metrics[surface] = DomainMetrics(surface=surface)
        return self._metrics[surface]

    def all(self) -> dict[str, DomainMetrics]:
        return dict(self._metrics)

    def reset(self, surface: str | None = None) -> None:
        if surface:
            self._metrics.pop(surface, None)
        else:
            self._metrics.clear()
