from __future__ import annotations

import pytest

from agent.cli import main as cli_main


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        ("init", ["--yes"], ("init", ["--yes"])),
        ("status", [], ("goals", ["--status"])),
        ("first-run", [], ("goals", ["--first-run"])),
        ("update", [], ("update", [])),
        ("ask", ["hello"], ("alias", ["ask", "hello"])),
        ("plan", ["hello"], ("alias", ["plan", "hello"])),
        ("analyze", ["hello"], ("alias", ["analyze", "hello"])),
        ("review", ["hello"], ("alias", ["review", "hello"])),
        ("diagnose", ["hello"], ("alias", ["diagnose", "hello"])),
        ("patch", ["hello"], ("alias", ["patch", "hello"])),
        ("repair-admin", ["hello"], ("alias", ["repair-admin", "hello"])),
        ("new-project", ["hello"], ("alias", ["new-project", "hello"])),
        ("evolve-project", ["hello"], ("alias", ["evolve-project", "hello"])),
        ("doctor", [], ("doctor", [])),
        ("tui", [], ("tui", [])),
        ("web", [], ("web", [])),
    ],
)
def test_commands_dispatch_to_expected_handlers(monkeypatch, command, args, expected) -> None:
    calls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(cli_main, "_run_init", lambda argv: calls.append(("init", list(argv))) or 0)
    monkeypatch.setattr(cli_main, "run_cli_goals", lambda argv: calls.append(("goals", list(argv))) or 0)
    monkeypatch.setattr(
        cli_main,
        "run_goal_alias",
        lambda alias, argv: calls.append(("alias", [alias, *list(argv)])) or 0,
    )
    monkeypatch.setattr(cli_main, "_run_doctor", lambda argv: calls.append(("doctor", list(argv))) or 0)
    monkeypatch.setattr(cli_main, "_run_update", lambda argv: calls.append(("update", list(argv))) or 0)
    monkeypatch.setattr(cli_main, "_run_tui", lambda argv: calls.append(("tui", list(argv))) or 0)
    monkeypatch.setattr(cli_main, "_run_web", lambda argv: calls.append(("web", list(argv))) or 0)

    rc = cli_main.main([command, *args])

    assert rc == 0
    assert calls == [expected]


@pytest.mark.parametrize(
    "command",
    [
        "init",
        "status",
        "first-run",
        "update",
        "ask",
        "plan",
        "analyze",
        "review",
        "diagnose",
        "patch",
        "repair-admin",
        "new-project",
        "evolve-project",
        "doctor",
        "tui",
        "web",
    ],
)
def test_commands_accept_help(monkeypatch, command) -> None:
    monkeypatch.setattr(cli_main, "_run_init", lambda _argv: 0)
    monkeypatch.setattr(cli_main, "run_cli_goals", lambda _argv: 0)
    monkeypatch.setattr(cli_main, "run_goal_alias", lambda _alias, _argv: 0)
    monkeypatch.setattr(cli_main, "_run_doctor", lambda _argv: 0)
    monkeypatch.setattr(cli_main, "_run_update", lambda _argv: 0)
    monkeypatch.setattr(cli_main, "_run_tui", lambda _argv: 0)
    monkeypatch.setattr(cli_main, "_run_web", lambda _argv: 0)

    rc = cli_main.main([command, "--help"])
    assert rc == 0


def test_unknown_command_returns_non_zero_and_help(capsys) -> None:
    rc = cli_main.main(["not-a-command"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "Unknown command" in captured.out
    assert "usage:" in captured.out


def test_goal_command_maps_goal_id_to_goal_detail(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli_main, "run_cli_goals", lambda argv: calls.append(list(argv)) or 0)

    rc = cli_main.main(["goal", "--goal-id", "goal-123"])

    assert rc == 0
    assert calls == [["--goal-detail", "goal-123"]]


def test_goal_command_keeps_goal_purge_unchanged(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli_main, "run_cli_goals", lambda argv: calls.append(list(argv)) or 0)

    rc = cli_main.main(["goal", "--goal-purge", "goal-123", "--yes"])

    assert rc == 0
    assert calls == [["--goal-purge", "goal-123", "--yes"]]
