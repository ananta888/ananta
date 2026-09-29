from __future__ import annotations

import json
from pathlib import Path


def test_hermes_read_only_routing_contract_from_scenario():
    data = json.loads(Path("scripts/scenarios/hermes_free_models_small_task.json").read_text(encoding="utf-8"))
    scenario = data["scenarios"][0]
    routing = scenario["expected_routing"]
    assert routing["plan_only"]["backend"] == "hermes"
    assert routing["review"]["backend"] == "hermes"
    assert routing["patch_propose"]["backend"] == "hermes"
    assert routing["patch_propose"]["must_not_apply_patch"] is True
    assert routing["coding"]["backend"] == "opencode"


def test_hermes_blocks_mutating_task_kinds_in_scenario():
    data = json.loads(Path("scripts/scenarios/hermes_free_models_small_task.json").read_text(encoding="utf-8"))
    blocked = set(data["scenarios"][0]["config_overrides"]["hermes_worker_adapter"]["blocked_task_kinds"])
    assert {"patch_apply", "command_execute"}.issubset(blocked)
