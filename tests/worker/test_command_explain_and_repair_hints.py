from __future__ import annotations

from worker.shell.command_explain import explain_command
from worker.shell.command_repair_hints import build_command_repair_hints


def test_command_explain_summarizes_command_without_execution() -> None:
    explanation = explain_command("pytest -q tests/worker")
    assert "pytest" in explanation["summary"]
    assert "approval-gated" in explanation["effects"]


def test_command_repair_hints_for_failed_test_command() -> None:
    hints = build_command_repair_hints(
        command="pytest -q",
        exit_code=1,
        stderr="assert 1 == 2",
    )
    assert any("first failing test" in hint.lower() for hint in hints)


def test_command_repair_hints_for_missing_dependency() -> None:
    hints = build_command_repair_hints(
        command="python -m pytest",
        exit_code=1,
        stderr="ModuleNotFoundError: No module named requests",
    )
    assert any("missing python module" in hint.lower() for hint in hints)
