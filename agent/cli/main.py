from __future__ import annotations

import argparse
import json
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
    "run",
    *GOAL_ALIAS_COMMANDS,
    "tui",
    "tmux",
    "doctor",
    "web",
    "voice-file",
    "prompt",
    "llm-log",
)
COMPAT_COMMANDS = ("goal", "goals")

# New domain groups registered under agent/cli/commands/
DOMAIN_COMMANDS = (
    "config",
    "runtime",
    "llm",
    "hub",
    "worker",
    "task",
    "project",
    "rag",
    "repair",
    "dev",
    "share",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta",
        description=(
            "Ananta — unified CLI for autonomous multi-agent workflows.\n\n"
            "User commands:\n"
            "  init, first-run, doctor, status, update\n"
            "  goal <action>    — create, list, inspect, status + shortcut aliases\n"
            "  task <action>    — inspect, list\n"
            "  prompt <action>  — inspect traces, render prompts, view reports\n"
            "  config <action>  — show, validate, setup-planning, apply-profile\n"
            "  llm <action>     — list, log\n"
            "  hub <action>     — status\n"
            "  worker <action>  — list, status\n"
            "  runtime <action> — list, inspect, recommend\n"
            "\n"
            "Developer/CI commands (not for end-users):\n"
            "  dev <action>     — acceptance, check, audit, validate, smoke, benchmark, e2e\n"
            "\n"
            "Shortcut aliases (map to 'ananta goal create'):\n"
            "  ask, plan, analyze, review, diagnose, patch, repair-admin, new-project, evolve-project\n"
        ),
        epilog=(
            "Examples:\n"
            "  ananta init --yes --runtime-mode local-dev --llm-backend ollama\n"
            "  ananta goal create \"Build a Fibonacci API\" --profile opencode_preconfigured\n"
            "  ananta goal list\n"
            "  ananta goal status <goal-id>\n"
            "  ananta config show\n"
            "  ananta config setup-planning\n"
            "  ananta llm log tail --limit 10\n"
            "  ananta prompt goal-traces --goal-id <id>\n"
            "  ananta dev acceptance --scenario-file scenario_lmstudio.json --sla-seconds 900\n"
            "  ananta dev check cycles\n"
            "  ananta ask \"What should I do next?\"\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="Top-level command or domain group (see above).",
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


def _run_ananta_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="ananta run")
    sub = parser.add_subparsers(dest="run_cmd")
    three = sub.add_parser("three-worker", help="Run the Hermes/OpenCode/Ananta-worker comparison with local planning")
    three.add_argument("--prompt", required=True)
    three.add_argument("--config", default=None)
    three.add_argument("--json", action="store_true")
    mode_group = three.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="Use deterministic dry-run executor (default)")
    mode_group.add_argument("--execute", action="store_true", help="Execute Hermes track and return handoff metadata for other tracks")

    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    if parsed.run_cmd != "three-worker":
        parser.print_help()
        return 2

    from agent.services.three_worker_comparison_runner import get_three_worker_comparison_runner
    from agent.services.three_worker_track_executor import get_three_worker_track_executor

    use_execute = bool(parsed.execute)
    track_executor = get_three_worker_track_executor() if use_execute else None

    result = get_three_worker_comparison_runner().run(
        prompt=parsed.prompt,
        config_path=parsed.config,
        env=dict(os.environ),
        track_executor=track_executor,
    ).as_dict()
    if parsed.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Run: {result['run_id']}")
        print(f"Status: {result['status']}")
        planning = result.get("planning") or {}
        print(f"Planning: {planning.get('provider')} / {planning.get('model')}")
        for track in result.get("tracks") or []:
            print(f"- {track.get('track_id')}: {track.get('status')} ({track.get('requested_backend')})")
    return 0 if result.get("status") == "ok" else 1


def _run_tmux(argv: Sequence[str]) -> int:
    from agent.cli.commands.tmux import dispatch
    return dispatch(argv)


def _run_tui(argv: Sequence[str]) -> int:
    # Editor/tool launch mode: intercept before routing to operator TUI
    argv_list = list(argv)
    if "--open" in argv_list or "--tool" in argv_list:
        from agent.cli.commands.tui_editor import dispatch as tui_editor_dispatch
        return tui_editor_dispatch(argv_list)

    if any(arg in {"-h", "--help"} for arg in argv):
        print("Usage: ananta tui [tui options]")
        print("Launches the operator TUI by default.")
        print("Startup splash is shown by default.")
        print("  --open <file>              Open file in configured editor")
        print("  --open <file> --with nvim  Open file with specific editor")
        print("  --open <file> --readonly   Open file in read-only mode")
        print("  --tool <tool-id>           Launch a TUI tool (e.g. git_ui)")
        print("  --skip-splash              Skip fullscreen startup splash")
        print("  --graphics <mode>          Graphics: auto|kitty|sixel|iterm2|halfblock|ascii|none")
        print("  --quality <q>              Quality: low|medium|high|ultra")
        print("  --frame-width <px>         Target frame width in pixels")
        print("  --frame-height <px>        Target frame height in pixels")
        print("  --target-fps <n>           Target FPS for pixel rendering")
        print("  --oversampling-factor <n>  SVG oversampling factor")
        print("  --force-pixel-graphics     Avoid ASCII fallback when possible")
        print("  --logo-renderer <mode>     Header logo renderer: auto|ansi|sixel|kitty|none")
        print("  --logo-animation <preset>  Header logo animation: static|pulse|shimmer|rotate_hint")
        print("  --logo-fps <n>             Header logo animation fps (1-16)")
        print("  --enable-3d                Enable offscreen 3D header scene")
        print("  --scene <id>               3D scene (e.g. demo-cube)")
        print("  --3d-renderer <mode>       3D renderer: auto|moderngl|raylib")
        print("  --no-logo                  Disable persistent header logo")
        print("  --legacy                   Use the previous report-style shell")
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
    from agent.cli.commands.prompt import dispatch
    return dispatch(argv)


def _run_task(argv: Sequence[str]) -> int:
    from agent.cli.commands.task import dispatch
    return dispatch(argv)


def _run_llm_log(argv: Sequence[str]) -> int:
    from agent.cli.commands.llm import dispatch_llm_log
    return dispatch_llm_log(argv)


def _run_compat_goals(argv: Sequence[str]) -> int:
    if not argv:
        print("Error: `ananta goal|goals` expects arguments.")
        print("Use `ananta goal list`, `ananta goal create ...`, or `ananta --help`.")
        return 2
    normalized = list(argv)
    if "--goal-id" in normalized and "--goal-detail" not in normalized and "--goal-purge" not in normalized:
        idx = normalized.index("--goal-id")
        normalized[idx] = "--goal-detail"
    if argv[0] in GOAL_ALIAS_COMMANDS:
        return run_goal_alias(argv[0], argv[1:])
    return run_cli_goals(normalized)


def _run_domain(command: str, argv: Sequence[str]) -> int:
    """Dispatch to a domain command module in agent/cli/commands/."""
    try:
        from agent.cli.commands import DOMAIN_MODULES
        mod = DOMAIN_MODULES.get(command)
        if mod is None:
            return None  # type: ignore[return-value]
        return mod.dispatch(list(argv))
    except Exception as exc:
        import sys
        print(f"Error: Domain command '{command}' failed to load: {exc}", file=sys.stderr)
        return 10


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(argv)

    command = parsed.command
    rest = list(parsed.args)
    if not command:
        from agent.cli.banner import print_banner
        print_banner()
        parser.print_help()
        return 0

    # Existing flat commands (backward-compat, highest priority)
    if command == "init":
        return _run_init(rest)
    if command == "status":
        from agent.cli.banner import print_banner
        print_banner()
        return run_cli_goals(["--status", *rest])
    if command == "first-run":
        return run_cli_goals(["--first-run", *rest])
    if command == "update":
        return _run_update(rest)
    if command == "run":
        return _run_ananta_run(rest)
    if command in GOAL_ALIAS_COMMANDS or command == "repair-script":
        return run_goal_alias(command, rest)
    if command == "doctor":
        return _run_doctor(rest)
    if command == "tui":
        return _run_tui(rest)
    if command == "tmux":
        return _run_tmux(rest)
    if command == "web":
        return _run_web(rest)
    if command == "voice-file":
        return _run_voice_file(rest)
    if command == "prompt":
        return _run_prompt(rest)
    if command == "task":
        return _run_task(rest)
    if command in COMPAT_COMMANDS:
        return _run_compat_goals(rest)
    if command == "llm-log":
        return _run_llm_log(rest)
    if command == "help":
        parser.print_help()
        return 0

    # Domain commands (new hierarchy)
    if command in DOMAIN_COMMANDS:
        result = _run_domain(command, rest)
        if result is not None:
            return result

    print(f"Error: Unknown command '{command}'")
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
