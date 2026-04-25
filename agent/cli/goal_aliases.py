from __future__ import annotations

from collections.abc import Sequence

from agent import cli_goals

GOAL_ALIAS_COMMANDS = tuple(cli_goals.SHORTCUT_GOALS.keys())


def _normalize_exit_code(code: object) -> int:
    if isinstance(code, int):
        return code
    return 1


def run_cli_goals(argv: Sequence[str]) -> int:
    try:
        result = cli_goals.main(list(argv))
    except SystemExit as exc:
        return _normalize_exit_code(exc.code)
    if result is None:
        return 0
    return int(result)


def run_goal_alias(command: str, argv: Sequence[str]) -> int:
    return run_cli_goals([command, *list(argv)])
