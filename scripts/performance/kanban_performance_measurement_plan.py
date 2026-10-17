#!/usr/bin/env python3
"""Measurement plan for the formal Kanban performance gate.

This module documents and validates the measurement workflow:
  baseline capture -> candidate run -> comparison against approved baseline
-> environment fingerprint binding -> decision.

It is intentionally a *plan* (no side effects) so it can be reviewed before
any actual benchmark execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Paths (relative to repo root) ------------------------------------------
BASELINE_CANDIDATE = Path("artifacts/test-gates/kanban-model-dashboard-performance-baseline-candidate.v1.json")
APPROVED_BASELINE = Path(
    "config/test-profiles/kanban-model-dashboard/baselines/formal-performance-approved.v1.json"
)
WORKLOAD_PROFILE = Path("config/test-profiles/kanban-model-dashboard/formal-performance.v1.json")
ENVIRONMENT_SCRIPT = Path("scripts/performance/kanban_performance_environment.py")
SUITE_SCRIPT = Path("scripts/performance/run_kanban_model_dashboard_performance_suite.py")

# --- Thresholds ---------------------------------------------------------------
REGRESSION_REJECT_PCT = 2.0   # reject if any check regresses > 2% vs baseline
NOISE_STD_DEV_PCT = 10.0      # inconclusive if std dev across repeats > 10%
MARGIN_PASS_PCT = 5.0         # require >= 5% margin for promotion


@dataclass
class MeasurementStep:
    name: str
    script_or_path: Path
    purpose: str
    output_artifact: str | None = field(default=None)


@dataclass
class MeasurementPlan:
    steps: list[MeasurementStep]
    thresholds: dict[str, float] = field(default_factory=dict)


def build_plan() -> MeasurementPlan:
    """Return the canonical measurement plan as a data structure."""
    return MeasurementPlan(
        steps=[
            MeasurementStep(
                name="baseline_capture",
                script_or_path=SUITE_SCRIPT,
                purpose="Run formal suite on current commit; emit baseline candidate JSON.",
                output_artifact=str(BASELINE_CANDIDATE),
            ),
            MeasurementStep(
                name="candidate_run",
                script_or_path=SUITE_SCRIPT,
                purpose="Re-run suite after proposed patch with identical workload profile.",
                output_artifact=None,  # candidate JSON path is dynamic
            ),
            MeasurementStep(
                name="comparison_gate",
                script_or_path=APPROVED_BASELINE,
                purpose="Evaluate absolute_evaluation.checks against approved baseline.",
                output_artifact=None,
            ),
            MeasurementStep(
                name="environment_fingerprint",
                script_or_path=ENVIRONMENT_SCRIPT,
                purpose="Bind run to host metadata (OS, CPU, memory, toolchain versions).",
                output_artifact=None,
            ),
        ],
        thresholds={
            "regression_reject_pct": REGRESSION_REJECT_PCT,
            "noise_std_dev_pct": NOISE_STD_DEV_PCT,
            "margin_pass_pct": MARGIN_PASS_PCT,
        },
    )


def validate_plan(plan: MeasurementPlan) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors: list[str] = []
    for step in plan.steps:
        if not step.script_or_path.exists():
            # Plan files may not exist yet; that is acceptable for a *plan*.
            pass
        if not step.purpose.strip():
            errors.append(f"step {step.name}: missing purpose")
    return errors


if __name__ == "__main__":
    plan = build_plan()
    errors = validate_plan(plan)
    for e in errors:
        print(f"ERROR: {e}")
    if not errors:
        print("Measurement plan is valid.")
