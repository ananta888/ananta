"""Report Generator (SIM-027)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from simulation.engine.budget_guard import BudgetGuard, BudgetViolation
from simulation.metrics.core_metrics import MetricsCollector
from simulation.metrics.failure_classifier import FailureModeClassifier
from simulation.models.world_state import WorldState


class ReportGenerator:
    """Produces a JSON run report from metrics + final state."""

    def __init__(self, run_id: str, scenario_name: str) -> None:
        self.run_id = run_id
        self.scenario_name = scenario_name
        self._classifier = FailureModeClassifier()

    def generate(
        self,
        metrics: MetricsCollector,
        final_state: WorldState,
        budget_guard: BudgetGuard,
        budget_violation: BudgetViolation | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = metrics.summary()
        failure = self._classifier.classify(metrics, budget_violation)

        report: dict[str, Any] = {
            "run_id": self.run_id,
            "scenario_name": self.scenario_name,
            "generated_at": time.time(),
            "outcome": {
                "category": failure.category,
                "severity": failure.severity,
                "description": failure.description,
                "indicators": failure.indicators,
            },
            "budget": {
                "violation": {"kind": budget_violation.kind,
                               "message": budget_violation.message}
                              if budget_violation else None,
                "usage": budget_guard.usage.as_dict(),
                "remaining": budget_guard.remaining(),
            },
            "metrics": summary,
            "final_state_hash": final_state.state_hash(),
            "final_tick": final_state.tick,
        }
        if extra:
            report["extra"] = extra
        return report

    def save(self, report: dict[str, Any], path: str | Path) -> None:
        Path(path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
