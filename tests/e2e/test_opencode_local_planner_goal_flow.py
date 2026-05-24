from __future__ import annotations

import json
from pathlib import Path


def test_opencode_local_planner_e2e_scenario_contract():
    data = json.loads(Path("scripts/scenarios/opencode_local_planner_e2e.json").read_text(encoding="utf-8"))
    assert data["id"] == "opencode_local_planner_e2e"
    routing = data["routing"]
    assert routing["planner_runtime"]["provider"]
    assert routing["worker_runtime"]["backend"] == "opencode"
    expected = set(data["goal"]["expected_outputs"])
    assert {"mini_slugify.py", "test_mini_slugify.py"}.issubset(expected)
