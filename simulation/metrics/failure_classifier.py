"""FailureModeClassifier (SIM-026)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulation.engine.budget_guard import BudgetViolation
from simulation.metrics.core_metrics import MetricsCollector


@dataclass
class FailureModeReport:
    category: str   # extinction | stagnation | budget | adapter | crime_spiral | healthy_end
    severity: str   # low | medium | high | critical
    description: str
    indicators: dict[str, Any]


class FailureModeClassifier:
    """Classifies how a simulation ended."""

    def classify(
        self,
        metrics: MetricsCollector,
        budget_violation: BudgetViolation | None,
    ) -> FailureModeReport:
        summary = metrics.summary()
        if not summary:
            return FailureModeReport("no_data", "low", "No metrics recorded", {})

        indicators: dict[str, Any] = {
            "final_living": summary.get("final_living", 0),
            "survival_rate_pct": summary.get("survival_rate_pct", 0),
            "total_deaths": summary.get("total_deaths", 0),
            "total_crimes": summary.get("total_crimes", 0),
            "ticks_run": summary.get("ticks_run", 0),
        }

        living = summary.get("final_living", 0)
        survival_pct = summary.get("survival_rate_pct", 100.0)
        crime_rate = summary.get("total_crimes", 0) / max(1, summary.get("ticks_run", 1))

        # Extinction
        if living == 0:
            return FailureModeReport(
                "extinction", "critical",
                "All agents died — population collapsed",
                indicators,
            )

        # Budget violation
        if budget_violation:
            if budget_violation.kind == "cost_usd":
                return FailureModeReport("budget", "high", budget_violation.message, indicators)
            if budget_violation.kind == "failures":
                return FailureModeReport("adapter", "high",
                                          "Too many consecutive adapter failures", indicators)
            return FailureModeReport("budget", "medium", budget_violation.message, indicators)

        # Crime spiral
        if crime_rate > 2.0:
            return FailureModeReport(
                "crime_spiral", "high",
                f"High crime rate: {crime_rate:.1f} crimes/tick",
                indicators,
            )

        # Low survival
        if survival_pct < 30:
            return FailureModeReport(
                "stagnation", "medium",
                f"Only {survival_pct:.0f}% agents survived",
                indicators,
            )

        return FailureModeReport(
            "healthy_end", "low",
            f"{living} agents alive, {survival_pct:.0f}% survival",
            indicators,
        )
