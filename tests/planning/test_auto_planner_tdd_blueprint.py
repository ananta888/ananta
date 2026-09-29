from __future__ import annotations

from agent.services.planning_utils import match_goal_template


def test_match_goal_template_returns_tdd_decomposition_sequence() -> None:
    subtasks = match_goal_template("Bitte TDD fuer einen Login-Bugfix mit Red Green Refactor planen")

    assert subtasks is not None
    assert len(subtasks) >= 7
    assert subtasks[0]["title"] == "Verhalten und Akzeptanzgrenzen klaeren"
    assert subtasks[1]["depends_on"] == ["1"]
    assert subtasks[2]["title"] == "Red-Phase ausfuehren und Evidenz sichern"
    assert subtasks[2]["depends_on"] == ["2"]
    assert subtasks[4]["title"] == "Green-Phase verifizieren"
    assert subtasks[4]["depends_on"] == ["4"]
    assert subtasks[5]["title"] == "Optional refactoren mit Sicherheitsnetz"
    assert subtasks[6]["title"] == "Finale Verifikation und Abschluss"


def test_tdd_template_describes_degraded_path_if_tests_cannot_run() -> None:
    subtasks = match_goal_template("test driven development plan fuer neue validierung")

    assert subtasks is not None
    red_phase = next(item for item in subtasks if item["title"] == "Red-Phase ausfuehren und Evidenz sichern")
    assert "degraded" in red_phase["description"].lower()
