from __future__ import annotations

from agent.cli import main as cli_main


def _run(argv: list[str]) -> int:
    try:
        result = cli_main.main(argv)
    except SystemExit as exc:  # argparse --help exits here
        if isinstance(exc.code, int):
            return exc.code
        return 1
    if result is None:
        return 0
    return int(result)


def test_unified_cli_help_smoke(capsys) -> None:
    command = ["--help"]
    rc = _run(command)

    out = capsys.readouterr().out
    assert rc == 0, "CLI smoke failed for command: ananta --help"
    assert "usage: ananta" in out


def test_unified_cli_init_help_smoke(capsys) -> None:
    command = ["init", "--help"]
    rc = _run(command)

    out = capsys.readouterr().out
    assert rc == 0, "CLI smoke failed for command: ananta init --help"
    assert "ananta init" in out


def test_unified_cli_status_help_smoke(capsys) -> None:
    command = ["status", "--help"]
    rc = _run(command)

    out = capsys.readouterr().out
    assert rc == 0, "CLI smoke failed for command: ananta status --help"
    assert "CLI for Ananta Goals" in out


def test_unified_cli_ask_help_smoke(capsys) -> None:
    command = ["ask", "--help"]
    rc = _run(command)

    out = capsys.readouterr().out
    assert rc == 0, "CLI smoke failed for command: ananta ask --help"
    assert "CLI for Ananta Goals" in out


def test_unified_cli_tui_help_smoke(capsys) -> None:
    command = ["tui", "--help"]
    rc = _run(command)

    out = capsys.readouterr().out
    assert rc == 0, "CLI smoke failed for command: ananta tui --help"
    assert "Usage: ananta tui" in out
