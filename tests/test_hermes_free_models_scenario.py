from __future__ import annotations

import json
from pathlib import Path


def test_single_hermes_free_models_scenario_is_valid() -> None:
    path = Path("todos/single_hermes_free_models_scenario.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data["scenarios"]
    assert isinstance(scenarios, list) and scenarios
    scenario = scenarios[0]
    assert scenario["id"] == "hermes_free_models_small_task"
    flags = scenario["config_overrides"]["feature_flags"]
    hermes_cfg = scenario["config_overrides"]["hermes_worker_adapter"]
    assert flags["enable_hermes_worker_adapter"] is True
    assert hermes_cfg["feature_flag_enabled"] is True
    assert "patch_apply" in hermes_cfg["blocked_task_kinds"]
    assert "command_execute" in hermes_cfg["blocked_task_kinds"]
    assert scenario["expected_routing"]["patch_propose"]["must_not_apply_patch"] is True
    assert "small" in str(scenario["test_goal"]["expected_complexity"]).lower()


def test_scenario_free_suffix_policy_matches_model_ids() -> None:
    path = Path("todos/single_hermes_free_models_scenario.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    scenario = data["scenarios"][0]
    hermes_cfg = scenario["config_overrides"]["hermes_worker_adapter"]
    policy = hermes_cfg["model_selection_policy"]
    assert policy["require_free_model_suffix"] is True
    models = [hermes_cfg["default_model"], *hermes_cfg["task_kind_models"].values(), *hermes_cfg["fallback_free_models"]]
    assert all(str(model).endswith(":free") for model in models)
