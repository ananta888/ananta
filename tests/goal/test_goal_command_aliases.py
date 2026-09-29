from __future__ import annotations

import pytest

from agent.cli import goal_aliases
from agent.cli import main as cli_main


@pytest.mark.parametrize(
    "shortcut",
    ["ask", "plan", "analyze", "review", "diagnose", "patch", "repair-admin", "new-project", "evolve-project"],
)
def test_goal_aliases_forward_argv_to_cli_goals(monkeypatch, shortcut) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_cli_goals_main(argv: list[str] | None = None):
        captured["argv"] = argv
        return None

    monkeypatch.setattr(goal_aliases.cli_goals, "main", fake_cli_goals_main)

    rc = goal_aliases.run_goal_alias(shortcut, ["check", "flow", "--team", "team-a", "--no-create"])

    assert rc == 0
    assert captured["argv"] == [shortcut, "check", "flow", "--team", "team-a", "--no-create"]


def test_main_routes_shortcut_to_goal_alias_runner(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_run_goal_alias(command: str, argv: list[str]) -> int:
        captured["argv"] = [command, *argv]
        return 0

    monkeypatch.setattr(cli_main, "run_goal_alias", fake_run_goal_alias)

    rc = cli_main.main(["review", "auth", "changes"])

    assert rc == 0
    assert captured["argv"] == ["review", "auth", "changes"]
