from __future__ import annotations

from pathlib import Path

import scripts.run_release_gate as run_release_gate


def test_planning_cleanup_violations_detect_hardcoded_literals() -> None:
    violations = run_release_gate._planning_cleanup_violations(
        planning_utils_text='GOAL_TEMPLATES = {"bug_fix": []}\n',
        teams_text='SEED_BLUEPRINTS = {"Scrum": {}}\nSCRUM_INITIAL_TASKS = []\n',
    )

    assert "hardcoded_goal_templates_literal_in_planning_utils" in violations
    assert "seed_blueprints_literal_in_routes_teams" in violations
    assert "initial_tasks_literal_in_routes_teams" in violations


def test_planning_cleanup_check_returns_actionable_reason_for_violation(tmp_path: Path) -> None:
    planning_utils_path = tmp_path / "agent" / "services" / "planning_utils.py"
    teams_path = tmp_path / "agent" / "routes" / "teams.py"
    planning_utils_path.parent.mkdir(parents=True, exist_ok=True)
    teams_path.parent.mkdir(parents=True, exist_ok=True)
    planning_utils_path.write_text('GOAL_TEMPLATES = {"legacy": []}\n', encoding="utf-8")
    teams_path.write_text('SEED_BLUEPRINTS = {"Scrum": {}}\n', encoding="utf-8")

    ok, reason = run_release_gate._check_planning_cleanup(tmp_path)

    assert ok is False
    assert "planning_cleanup_violation" in reason
    assert "PlanningTemplateCatalog" in reason
    assert "SeedBlueprintCatalog" in reason


def test_planning_cleanup_check_allows_catalog_backed_code(tmp_path: Path) -> None:
    planning_utils_path = tmp_path / "agent" / "services" / "planning_utils.py"
    teams_path = tmp_path / "agent" / "routes" / "teams.py"
    planning_utils_path.parent.mkdir(parents=True, exist_ok=True)
    teams_path.parent.mkdir(parents=True, exist_ok=True)
    planning_utils_path.write_text("GOAL_TEMPLATES = _load_goal_templates_from_catalog()\n", encoding="utf-8")
    teams_path.write_text("seed_blueprints = get_seed_blueprint_catalog().as_seed_blueprint_map()\n", encoding="utf-8")

    ok, reason = run_release_gate._check_planning_cleanup(tmp_path)

    assert ok is True
    assert reason == "ok"
