from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from agent.cli import init_wizard
from agent.cli.doctor import main as doctor_main
from agent.cli.goal_aliases import GOAL_ALIAS_COMMANDS, run_cli_goals, run_goal_alias

CORE_COMMANDS = (
    "init",
    "first-run",
    "status",
    *GOAL_ALIAS_COMMANDS,
    "tui",
    "doctor",
    "web",
)
COMPAT_COMMANDS = ("goal", "goals")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta",
        description="Unified CLI entrypoint for common Ananta workflows.",
        epilog=(
            "Examples:\n"
            "  ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default\n"
            "  ananta status\n"
            "  ananta ask \"What should I do next?\"\n"
            "  ananta review \"Review auth changes\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        help=(
            "Command: init, first-run, status, ask, plan, analyze, review, diagnose, "
            "patch, repair-admin, new-project, evolve-project, tui, doctor, web"
        ),
    )
    parser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _normalize_exit_code(code: object) -> int:
    if isinstance(code, int):
        return code
    return 1


def _invoke(entrypoint, argv: Sequence[str]) -> int:
    try:
        result = entrypoint(list(argv))
    except SystemExit as exc:
        return _normalize_exit_code(exc.code)
    if result is None:
        return 0
    return int(result)


def _run_init(argv: Sequence[str]) -> int:
    return _invoke(init_wizard.main, argv)


def _run_doctor(argv: Sequence[str]) -> int:
    return _invoke(doctor_main, argv)


def _run_tui(argv: Sequence[str]) -> int:
    if any(arg in {"-h", "--help"} for arg in argv):
        print("Usage: ananta tui [tui options]")
        print("Launches the existing runtime TUI surface entrypoint.")
        return 0
    try:
        from client_surfaces.tui_runtime.ananta_tui.app import main as tui_main
    except ModuleNotFoundError as exc:
        print(f"Error: TUI launcher is unavailable ({exc}).")
        print("Run `ananta doctor` and verify optional TUI dependencies.")
        return 2
    return _invoke(tui_main, argv)


def _run_web(argv: Sequence[str]) -> int:
    if any(arg in {"-h", "--help"} for arg in argv):
        print("Usage: ananta web [--url <web-url>]")
        print("Prints the configured Web UI URL when available.")
        return 0

    url_override = None
    if len(argv) == 2 and argv[0] == "--url":
        url_override = argv[1]
    elif argv:
        print("Error: `ananta web` only supports optional `--url <web-url>`.")
        return 2

    configured_url = url_override or os.getenv("ANANTA_WEB_URL") or "http://localhost:4200"
    print(f"Web UI URL: {configured_url}")
    print("Open it in your browser, or set ANANTA_WEB_URL for a different target.")
    if configured_url == "http://localhost:4200":
        print("If unreachable, start the frontend stack as described in README/docs.")
    return 0


def _run_compat_goals(argv: Sequence[str]) -> int:
    if not argv:
        print("Error: `ananta goal|goals` expects arguments.")
        print("Use `ananta status`, `ananta ask ...`, or `ananta --help`.")
        return 2
    if argv[0] in GOAL_ALIAS_COMMANDS:
        return run_goal_alias(argv[0], argv[1:])
    return run_cli_goals(argv)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(argv)

    command = parsed.command
    rest = list(parsed.args)
    if not command:
        parser.print_help()
        return 0

    if command == "init":
        return _run_init(rest)
    if command == "status":
        return run_cli_goals(["--status", *rest])
    if command == "first-run":
        return run_cli_goals(["--first-run", *rest])
    if command in GOAL_ALIAS_COMMANDS:
        return run_goal_alias(command, rest)
    if command == "doctor":
        return _run_doctor(rest)
    if command == "tui":
        return _run_tui(rest)
    if command == "web":
        return _run_web(rest)
    if command in COMPAT_COMMANDS:
        return _run_compat_goals(rest)
    if command == "help":
        parser.print_help()
        return 0

    print(f"Error: Unknown command '{command}'")
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
