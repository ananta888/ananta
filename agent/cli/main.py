from __future__ import annotations

import argparse
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
        print("Usage: ananta web")
        print("Web launcher is optional and not wired in this build.")
        return 0
    print("Error: `ananta web` is not wired in this build yet.")
    print("Use the existing frontend startup flow from README/docs.")
    return 2


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
        return run_cli_goals(rest)
    if command == "help":
        parser.print_help()
        return 0

    print(f"Error: Unknown command '{command}'")
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
