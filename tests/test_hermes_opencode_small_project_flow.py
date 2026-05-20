from __future__ import annotations

import json
from pathlib import Path


def test_hermes_opencode_small_project_flow_routing_contract() -> None:
    data = json.loads(Path("todos/single_hermes_free_models_scenario.json").read_text(encoding="utf-8"))
    scenario = data["scenarios"][0]
    routing = scenario["expected_routing"]
    assert routing["plan_only"]["backend"] == "hermes"
    assert routing["review"]["backend"] == "hermes"
    assert routing["patch_propose"]["backend"] == "hermes"
    assert routing["coding"]["backend"] == "opencode"
    assert routing["coding"]["model"] == "Big Pickel"
