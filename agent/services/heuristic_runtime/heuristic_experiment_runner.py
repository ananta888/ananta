"""HeuristicExperimentRunner — führt Shadow-Mode A/B-Tests durch.

Shadow Runner berechnet Entscheidungen parallel, schreibt sie NICHT in sichtbare Snake-Bewegung.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

try:
    from agent.services.heuristic_runtime.dsl.evaluator import DslEvaluator
    from agent.services.heuristic_runtime.dsl.validator import DslValidator
    _RUNTIME_AVAILABLE = True
except ImportError:
    _RUNTIME_AVAILABLE = False

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult


@dataclass
class ShadowRunResult:
    heuristic_id: str
    shadow_action: str
    shadow_confidence: float
    active_action: str
    active_confidence: float
    target_match: bool = False
    jitter_score: float = 0.0
    safety_violations: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heuristic_id": self.heuristic_id,
            "shadow_action": self.shadow_action,
            "shadow_confidence": self.shadow_confidence,
            "active_action": self.active_action,
            "active_confidence": self.active_confidence,
            "target_match": self.target_match,
            "jitter_score": self.jitter_score,
            "safety_violations": self.safety_violations,
            "timestamp": self.timestamp,
        }


@dataclass
class ExperimentReport:
    experiment_id: str
    heuristic_id: str
    total_ticks: int = 0
    target_match_count: int = 0
    safety_violation_count: int = 0
    average_confidence: float = 0.0
    jitter_score: float = 0.0
    results: list[ShadowRunResult] = field(default_factory=list)

    @property
    def target_match_rate(self) -> float:
        return self.target_match_count / max(1, self.total_ticks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "heuristic_id": self.heuristic_id,
            "total_ticks": self.total_ticks,
            "target_match_rate": round(self.target_match_rate, 3),
            "safety_violation_count": self.safety_violation_count,
            "average_confidence": round(self.average_confidence, 3),
            "jitter_score": round(self.jitter_score, 3),
        }


class HeuristicExperimentRunner:
    """Shadow Runner: evaluiert Heuristiken parallel ohne UI-Einfluss."""

    def __init__(self) -> None:
        self._evaluator = DslEvaluator() if _RUNTIME_AVAILABLE else None
        self._validator = DslValidator() if _RUNTIME_AVAILABLE else None
        self._reports: dict[str, ExperimentReport] = {}

    def run_shadow_tick(
        self,
        dsl: dict[str, Any],
        ctx: DecisionContext,
        active_result: DecisionResult,
        *,
        heuristic_id: str = "unknown",
        experiment_id: str = "exp_0",
    ) -> ShadowRunResult:
        """Berechnet Shadow-Entscheidung. Schreibt NICHT in sichtbare Snake-Bewegung."""
        if not _RUNTIME_AVAILABLE or self._evaluator is None:
            return ShadowRunResult(
                heuristic_id=heuristic_id,
                shadow_action="no_action", shadow_confidence=0.0,
                active_action=active_result.action_kind, active_confidence=active_result.confidence,
            )

        try:
            eval_result = self._evaluator.evaluate(dsl, ctx)
            shadow_action = eval_result.action.get("kind", "no_action") if eval_result.matched else "no_action"
            shadow_conf = eval_result.score if eval_result.matched else 0.0
        except Exception as e:
            _log.debug("ShadowRunner error (no UI impact): %s", e)
            shadow_action = "no_action"
            shadow_conf = 0.0

        target_match = shadow_action == active_result.action_kind

        result = ShadowRunResult(
            heuristic_id=heuristic_id,
            shadow_action=shadow_action,
            shadow_confidence=shadow_conf,
            active_action=active_result.action_kind,
            active_confidence=active_result.confidence,
            target_match=target_match,
        )

        # Report aktualisieren
        if experiment_id not in self._reports:
            self._reports[experiment_id] = ExperimentReport(
                experiment_id=experiment_id, heuristic_id=heuristic_id
            )
        report = self._reports[experiment_id]
        report.total_ticks += 1
        if target_match:
            report.target_match_count += 1
        report.results.append(result)

        return result

    def get_report(self, experiment_id: str) -> ExperimentReport | None:
        return self._reports.get(experiment_id)

    def active_decision_unchanged(self, active_result: DecisionResult) -> DecisionResult:
        """Gibt active_result unverändert zurück — Shadow läuft parallel, beeinflusst nichts."""
        return active_result
