"""TRM-004: Unit tests for pass/fail criteria logic, scenario loading, and report serialization."""
from __future__ import annotations

import json
import sys
import pathlib
import pytest


def _load_runner():
    mod_name = "first_goal_acceptance_runner"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        mod_name,
        pathlib.Path(__file__).parent.parent / "scripts" / "first_goal_acceptance_runner.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_runner()


# Pass/Fail criteria logic
class TestCriteriaLogic:
    def test_run_report_passes_when_all_criteria_pass(self, mod):
        report = mod.RunReport(run_index=1)
        report.criteria.append(mod.CriterionResult(1, "c1", True, "ok"))
        report.criteria.append(mod.CriterionResult(2, "c2", True, "ok"))
        assert report.passed is True

    def test_run_report_fails_when_any_criterion_fails(self, mod):
        report = mod.RunReport(run_index=1)
        report.criteria.append(mod.CriterionResult(1, "c1", True, "ok"))
        report.criteria.append(mod.CriterionResult(2, "c2", False, "failed"))
        assert report.passed is False

    def test_run_report_passes_with_no_criteria(self, mod):
        report = mod.RunReport(run_index=1)
        assert report.passed is True

    def test_criterion_stable_ids_cover_all_expected_criteria(self, mod):
        expected = {
            "goal_ingestion",
            "task_materialization",
            "autopilot_takeover",
            "no_planning_deadlock",
            "provider_stability",
            "write_phase_reached",
            "verification_present",
            "terminal_goal_status",
            "no_manual_intervention",
        }
        actual = set(mod._CRITERION_STABLE_IDS.values())
        assert expected.issubset(actual)


# Scenario loading (delegates to test_acceptance_runner_scenarios.py,
# but confirms integration with the runner module)
class TestScenarioLoading:
    def test_load_scenarios_from_file_is_callable(self, mod):
        assert callable(mod.load_scenarios_from_file)

    def test_load_valid_scenarios(self, mod, tmp_path):
        f = tmp_path / "scenarios.json"
        f.write_text(json.dumps({
            "scenarios": [
                {"id": "s1", "label": "Scenario 1", "config_profile": "ananta_ollama_local"},
                {"id": "s2", "label": "Scenario 2", "config_profile": "opencode_ollama_local"},
            ]
        }))
        result = mod.load_scenarios_from_file(str(f))
        assert len(result) == 2
        assert result[0]["id"] == "s1"
        assert result[1]["id"] == "s2"

    def test_invalid_scenario_file_raises_system_exit(self, mod, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{}")
        with pytest.raises(SystemExit):
            mod.load_scenarios_from_file(str(f))


# Report serialization
class TestReportSerialization:
    def test_aggregate_returns_required_keys(self, mod):
        summary = mod.aggregate([])
        for key in ("schema", "total_runs", "completed_runs", "write_phase_runs",
                    "autopilot_progress_runs", "repeatability_pass"):
            assert key in summary, f"Missing key: {key}"

    def test_aggregate_counts_completed_runs(self, mod):
        r1 = mod.RunReport(run_index=1, final_goal_status="completed")
        r2 = mod.RunReport(run_index=2, final_goal_status="failed")
        r3 = mod.RunReport(run_index=3, final_goal_status="completed")
        summary = mod.aggregate([r1, r2, r3])
        assert summary["total_runs"] == 3
        assert summary["completed_runs"] == 2

    def test_aggregate_repeatability_requires_two_completed(self, mod):
        r1 = mod.RunReport(run_index=1, final_goal_status="completed")
        r1.criteria.append(mod.CriterionResult(6, "write", True, ""))
        summary = mod.aggregate([r1])
        # Only 1 completed run → repeatability should be False
        assert summary["repeatability_pass"] is False

    def test_criterion_result_dict_includes_stable_id(self, mod):
        c = mod.CriterionResult(1, "Goal-Ingestion", True, "ok")
        d = {**c.__dict__, "criterion_id": c.criterion_id}
        assert d["criterion_id"] == "goal_ingestion"
        assert d["passed"] is True

    def test_run_report_serializes_ci_and_skipped_checks(self, mod):
        report = mod.RunReport(run_index=1, ci_safe_mode=True, skipped_checks=["provider_stability"])
        assert report.ci_safe_mode is True
        assert "provider_stability" in report.skipped_checks

    def test_report_schema_version_in_aggregate(self, mod):
        summary = mod.aggregate([])
        assert summary["schema"] == mod.REPORT_SCHEMA_VERSION
        assert "v" in mod.REPORT_SCHEMA_VERSION
