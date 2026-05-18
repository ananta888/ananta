from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    runner_path = Path(__file__).resolve().parents[2] / "scripts" / "first_goal_acceptance_runner.py"
    spec = importlib.util.spec_from_file_location("first_goal_acceptance_runner", runner_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_report_contains_learning_metrics():
    mod = _load_runner()
    r = mod.RunReport(run_index=1, final_goal_status="completed", planning_run_id="pr1", planning_parse_mode="strict_json", planning_repair_attempt_count=1)
    summary = mod.aggregate([r])
    assert "completed_runs" in summary
    assert "early_analysis_runs" in summary


def test_early_analysis_mode_outputs_classification():
    mod = _load_runner()
    r = mod.RunReport(run_index=1, early_analysis={"classification": "planning_stuck"})
    summary = mod.aggregate([r])
    assert summary["early_analysis_runs"] == 1
    assert summary["early_classification"]["planning_stuck"] == 1
