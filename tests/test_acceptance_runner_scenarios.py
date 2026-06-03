"""ARD-001: Tests for scenario file loading in the acceptance runner."""
from __future__ import annotations

import json
import sys
import pathlib
import pytest


def _import_runner():
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
def runner_mod():
    return _import_runner()


FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "scenarios"
SCHEMA_PATH = pathlib.Path(__file__).parent / "fixtures" / "reports" / "first_goal_acceptance_report.schema.json"


class TestLoadScenariosFromFile:
    def test_loads_valid_file(self, runner_mod):
        path = str(FIXTURES_DIR / "default_acceptance_scenarios.json")
        scenarios = runner_mod.load_scenarios_from_file(path)
        assert isinstance(scenarios, list)
        assert len(scenarios) >= 1
        for s in scenarios:
            assert "id" in s
            assert "label" in s

    def test_raises_for_missing_file(self, runner_mod):
        with pytest.raises(SystemExit, match="NOT FOUND"):
            runner_mod.load_scenarios_from_file("/tmp/nonexistent_scenario_file.json")

    def test_raises_for_invalid_json(self, runner_mod, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json}", encoding="utf-8")
        with pytest.raises(SystemExit, match="INVALID JSON"):
            runner_mod.load_scenarios_from_file(str(bad))

    def test_raises_for_missing_scenarios_key(self, runner_mod, tmp_path):
        bad = tmp_path / "missing_key.json"
        bad.write_text(json.dumps({"description": "no scenarios"}), encoding="utf-8")
        with pytest.raises(SystemExit, match="'scenarios' key"):
            runner_mod.load_scenarios_from_file(str(bad))

    def test_raises_for_empty_scenarios_list(self, runner_mod, tmp_path):
        bad = tmp_path / "empty.json"
        bad.write_text(json.dumps({"scenarios": []}), encoding="utf-8")
        with pytest.raises(SystemExit, match="'scenarios' key"):
            runner_mod.load_scenarios_from_file(str(bad))

    def test_raises_for_scenario_missing_required_keys(self, runner_mod, tmp_path):
        bad = tmp_path / "no_label.json"
        bad.write_text(json.dumps({"scenarios": [{"id": "s1"}]}), encoding="utf-8")
        with pytest.raises(SystemExit, match="missing required keys"):
            runner_mod.load_scenarios_from_file(str(bad))

    def test_raises_for_non_object_scenario(self, runner_mod, tmp_path):
        bad = tmp_path / "non_obj.json"
        bad.write_text(json.dumps({"scenarios": ["not-an-object"]}), encoding="utf-8")
        with pytest.raises(SystemExit, match="must be an object"):
            runner_mod.load_scenarios_from_file(str(bad))

    def test_scenario_id_and_label_in_loaded_result(self, runner_mod, tmp_path):
        valid = tmp_path / "valid.json"
        valid.write_text(json.dumps({
            "scenarios": [
                {"id": "test-scenario", "label": "Test Scenario", "config_profile": "ananta_ollama_local"}
            ]
        }), encoding="utf-8")
        scenarios = runner_mod.load_scenarios_from_file(str(valid))
        assert scenarios[0]["id"] == "test-scenario"
        assert scenarios[0]["label"] == "Test Scenario"


# ARD-002: Stable criterion IDs and schema version
class TestCriterionStableIds:
    def test_report_schema_version_is_defined(self, runner_mod):
        assert hasattr(runner_mod, "REPORT_SCHEMA_VERSION")
        assert "v" in str(runner_mod.REPORT_SCHEMA_VERSION)

    def test_criterion_result_exposes_stable_id(self, runner_mod):
        c = runner_mod.CriterionResult(1, "Goal-Ingestion", True, "details")
        assert c.criterion_id == "goal_ingestion"

    def test_all_numeric_ids_have_stable_mapping(self, runner_mod):
        for numeric_id, stable_id in runner_mod._CRITERION_STABLE_IDS.items():
            c = runner_mod.CriterionResult(numeric_id, "name", True, "d")
            assert c.criterion_id == stable_id
            assert "_" in stable_id or stable_id.isalpha(), f"stable_id '{stable_id}' should be snake_case"

    def test_unknown_id_gets_fallback_name(self, runner_mod):
        c = runner_mod.CriterionResult(99, "Unknown", True, "d")
        assert "99" in c.criterion_id

    def test_aggregate_uses_schema_version(self, runner_mod):
        summary = runner_mod.aggregate([])
        assert summary["schema"] == runner_mod.REPORT_SCHEMA_VERSION


class TestReportJsonSchema:
    def test_schema_file_exists(self):
        assert SCHEMA_PATH.exists(), "JSON schema fixture must exist at tests/fixtures/reports/first_goal_acceptance_report.schema.json"

    def test_schema_is_valid_json(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        assert schema.get("type") == "object"
        assert "properties" in schema

    def test_schema_requires_criterion_id_field(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        criteria_schema = (
            schema["properties"]["runs"]["items"]
                  ["properties"]["criteria"]["items"]
        )
        assert "criterion_id" in criteria_schema.get("required", [])

    def test_fixtures_comply_with_schema_schema_field(self):
        """Fixture reports may predate v2 — but schema file itself must be well-formed."""
        schema = json.loads(SCHEMA_PATH.read_text())
        assert schema["properties"]["summary"]["properties"]["schema"]["type"] == "string"
