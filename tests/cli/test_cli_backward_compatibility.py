from __future__ import annotations

from types import SimpleNamespace

from agent import cli_goals
from agent.cli import main as unified_cli


def test_module_cli_goals_status_path_still_works(capsys) -> None:
    paths: list[str] = []

    def fake_request(method, path, **_kwargs):
        paths.append(path)
        return SimpleNamespace(status_code=200, json=lambda: {"data": {}}, text="")

    result = cli_goals.main(["--status"], deps=cli_goals.CliGoalsDependencies(request=fake_request))

    assert result is None
    assert paths == ["/goals/readiness", "/tasks/auto-planner/status"]
    out = capsys.readouterr().out
    assert "Goal Readiness:" in out
    assert "Auto-Planner Status:" in out


def test_unified_cli_status_path_still_routes_to_goals(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_run_cli_goals(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(unified_cli, "run_cli_goals", fake_run_cli_goals)

    rc = unified_cli.main(["status"])

    assert rc == 0
    assert captured["argv"] == ["--status"]
