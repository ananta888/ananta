from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from agent.cli import init_wizard
from agent.cli.doctor import main as doctor_main
from agent.cli.goal_aliases import GOAL_ALIAS_COMMANDS, run_cli_goals, run_goal_alias
from agent.cli.update import main as update_main
from agent.cli.voice_file import main as voice_file_main

CORE_COMMANDS = (
    "init",
    "first-run",
    "status",
    "update",
    *GOAL_ALIAS_COMMANDS,
    "tui",
    "doctor",
    "web",
    "voice-file",
    "prompt",
    "llm-log",
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
            "  ananta update --help\n"
            "  ananta ask \"What should I do next?\"\n"
            "  ananta review \"Review auth changes\"\n"
            "  ananta llm-log tail --limit 10\n"
            "  ananta prompt inspect --trace-id <id>\n"
            "  ananta prompt render --mode generic --goal \"Build a CLI tool\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        help=(
            "Command: init, first-run, status, ask, plan, analyze, review, diagnose, "
            "patch, repair-admin, new-project, evolve-project, update, tui, doctor, web, "
            "voice-file, prompt, llm-log"
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


def _run_update(argv: Sequence[str]) -> int:
    return _invoke(update_main, argv)


def _run_tui(argv: Sequence[str]) -> int:
    if any(arg in {"-h", "--help"} for arg in argv):
        print("Usage: ananta tui [tui options]")
        print("Launches the operator TUI by default.")
        print("Use `ananta tui --legacy` for the previous report-style shell.")
        return 0
    use_legacy = "--legacy" in argv or "--fixture" in argv
    if not use_legacy:
        rest = [arg for arg in argv if arg != "--operator"]
        try:
            from client_surfaces.operator_tui.app import main as operator_tui_main
        except ModuleNotFoundError as exc:
            print(f"Error: Operator TUI launcher is unavailable ({exc}).")
            print("Run `ananta doctor` and verify TUI packaging.")
            return 2
        return _invoke(operator_tui_main, rest)
    argv = [arg for arg in argv if arg != "--legacy"]
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


def _run_voice_file(argv: Sequence[str]) -> int:
    return _invoke(voice_file_main, argv)


def _run_prompt(argv: Sequence[str]) -> int:
    from agent.cli.prompt_inspect import run_prompt_command
    sub_parser = argparse.ArgumentParser(prog="ananta prompt")
    sub_sub = sub_parser.add_subparsers(dest="prompt_cmd")

    # Add subcommands directly (not wrapped in extra "prompt" layer)
    inspect_p = sub_sub.add_parser("inspect", help="Show a specific prompt trace")
    inspect_p.add_argument("--trace-id", dest="trace_id", required=True)
    inspect_p.add_argument("--json", action="store_true")
    inspect_p.add_argument("--raw", action="store_true")
    inspect_p.add_argument("--full", action="store_true")

    render_p = sub_sub.add_parser("render", help="Render a planning prompt without calling a provider")
    render_p.add_argument("--mode", default="generic")
    render_p.add_argument("--goal", default="Test goal")
    render_p.add_argument("--language", default="de")
    render_p.add_argument("--model-family", dest="model_family")
    render_p.add_argument("--context-file", dest="context_file")
    render_p.add_argument("--preferred-output-format", dest="preferred_output_format", default="json")
    render_p.add_argument("--save-trace", dest="save_trace", action="store_true")
    render_p.add_argument("--json", action="store_true")

    gt_p = sub_sub.add_parser("goal-traces", help="Show all traces for a goal")
    gt_p.add_argument("--goal-id", dest="goal_id", required=True)
    gt_p.add_argument("--json", action="store_true")

    if not argv or argv[0] in ("-h", "--help"):
        sub_parser.print_help()
        return 0
    try:
        parsed = sub_parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    return run_prompt_command(parsed)


def _run_llm_log(argv: Sequence[str]) -> int:
    from agent.cli.prompt_inspect import run_llm_log_command
    sub_parser = argparse.ArgumentParser(prog="ananta llm-log")
    sub_sub = sub_parser.add_subparsers(dest="llm_log_cmd")

    tail_p = sub_sub.add_parser("tail", help="Show recent LLM requests")
    tail_p.add_argument("--limit", type=int, default=20)
    tail_p.add_argument("--provider")
    tail_p.add_argument("--model")
    tail_p.add_argument("--goal-id", dest="goal_id")
    tail_p.add_argument("--task-id", dest="task_id")
    tail_p.add_argument("--json", action="store_true")

    if not argv or argv[0] in ("-h", "--help"):
        sub_parser.print_help()
        return 0
    try:
        parsed = sub_parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    return run_llm_log_command(parsed)


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
    if command == "update":
        return _run_update(rest)
    if command in GOAL_ALIAS_COMMANDS or command == "repair-script":
        return run_goal_alias(command, rest)
    if command == "doctor":
        return _run_doctor(rest)
    if command == "tui":
        return _run_tui(rest)
    if command == "web":
        return _run_web(rest)
    if command == "voice-file":
        return _run_voice_file(rest)
    if command in COMPAT_COMMANDS:
        return _run_compat_goals(rest)
    if command == "prompt":
        return _run_prompt(rest)
    if command == "llm-log":
        return _run_llm_log(rest)
    if command == "help":
        parser.print_help()
        return 0

    print(f"Error: Unknown command '{command}'")
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
