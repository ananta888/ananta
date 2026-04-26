from __future__ import annotations

from agent.services.execution_focused_planning import (
    EXECUTION_FOCUSED_GOAL_HINTS,
    build_execution_focused_goal_template,
    match_execution_focused_goal_template,
)


def test_build_execution_focused_goal_template_handles_fibonacci_subject() -> None:
    subtasks = build_execution_focused_goal_template("Implement Python Fibonacci helper")

    assert len(subtasks) == 4
    assert "Fibonacci" in subtasks[0]["title"]
    assert subtasks[1]["depends_on"] == ["1"]
    assert subtasks[2]["depends_on"] == ["2"]
    assert subtasks[3]["depends_on"] == ["3"]


def test_match_execution_focused_goal_template_returns_none_without_hint() -> None:
    assert match_execution_focused_goal_template("plan strategic roadmap with no coding hints") is None


def test_match_execution_focused_goal_template_detects_hint() -> None:
    assert "python" in EXECUTION_FOCUSED_GOAL_HINTS
    subtasks = match_execution_focused_goal_template("Please add python tests and changed files summary")

    assert subtasks is not None
    assert any("pytest" in str(item.get("description") or "").lower() for item in subtasks)
