from __future__ import annotations

from agent import cli_goals
from agent.cli import main as unified_cli


def test_module_cli_goals_status_path_still_works(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_goals, "show_status", lambda: calls.append("status"))

    result = cli_goals.main(["--status"])

    assert result is None
    assert calls == ["status"]


def test_unified_cli_status_path_still_routes_to_goals(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_run_cli_goals(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(unified_cli, "run_cli_goals", fake_run_cli_goals)

    rc = unified_cli.main(["status"])

    assert rc == 0
    assert captured["argv"] == ["--status"]
