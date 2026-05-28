"""Heuristic Simulator — simuliert DSL-Kandidaten gegen aufgezeichnete Snapshots."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from agent.services.heuristic_runtime.dsl.evaluator import DslEvaluator
    from agent.services.heuristic_runtime.dsl.validator import DslValidator
    _RUNTIME_AVAILABLE = True
except ImportError:
    _RUNTIME_AVAILABLE = False

from agent.services.heuristic_runtime.decision_context import DecisionContext


@dataclass
class SimulationMetrics:
    total_frames: int = 0
    hit_count: int = 0
    no_action_count: int = 0
    error_count: int = 0
    confidence_sum: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.hit_count / max(1, self.total_frames)

    @property
    def no_action_rate(self) -> float:
        return self.no_action_count / max(1, self.total_frames)

    @property
    def average_confidence(self) -> float:
        return self.confidence_sum / max(1, self.hit_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_frames": self.total_frames,
            "hit_count": self.hit_count,
            "hit_rate": round(self.hit_rate, 3),
            "no_action_count": self.no_action_count,
            "no_action_rate": round(self.no_action_rate, 3),
            "error_count": self.error_count,
            "average_confidence": round(self.average_confidence, 3),
        }


@dataclass
class SimulationResult:
    proposal_id: str
    passed: bool
    metrics: SimulationMetrics = field(default_factory=SimulationMetrics)
    rejection_reason: str | None = None
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "passed": self.passed,
            "metrics": self.metrics.to_dict(),
            "rejection_reason": self.rejection_reason,
            "validation_errors": self.validation_errors,
        }


class HeuristicSimulator:
    def __init__(self) -> None:
        self._validator = DslValidator() if _RUNTIME_AVAILABLE else None
        self._evaluator = DslEvaluator() if _RUNTIME_AVAILABLE else None

    def simulate(
        self,
        dsl: dict[str, Any],
        snapshot_frames: list[dict[str, Any]],
        *,
        proposal_id: str = "unknown",
        min_hit_rate: float = 0.1,
    ) -> SimulationResult:
        """Simuliert DSL gegen Liste von Snapshot-Frames."""
        if not _RUNTIME_AVAILABLE:
            return SimulationResult(proposal_id=proposal_id, passed=False,
                                  rejection_reason="DSL runtime nicht verfügbar")

        # Validierung zuerst
        val = self._validator.validate(dsl)
        if not val.passed:
            return SimulationResult(proposal_id=proposal_id, passed=False,
                                  validation_errors=val.errors,
                                  rejection_reason="Validation fehlgeschlagen")

        metrics = SimulationMetrics()

        for frame in snapshot_frames:
            ctx = DecisionContext(
                source_surface="tui_snake",
                tui_snapshot_ref=frame.get("screen_hash"),
                active_panel=frame.get("active_panel"),
            )
            metrics.total_frames += 1
            try:
                eval_result = self._evaluator.evaluate(dsl, ctx)
                if eval_result.rejected:
                    metrics.error_count += 1
                elif eval_result.matched:
                    metrics.hit_count += 1
                    metrics.confidence_sum += eval_result.score
                else:
                    metrics.no_action_count += 1
            except Exception:
                metrics.error_count += 1

        passed = metrics.hit_rate >= min_hit_rate and metrics.error_count == 0
        return SimulationResult(
            proposal_id=proposal_id,
            passed=passed,
            metrics=metrics,
            rejection_reason=None if passed else f"hit_rate={metrics.hit_rate:.2f} < {min_hit_rate}",
        )
