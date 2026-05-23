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
            "  ananta run three-worker --prompt \"Analyze the last commits\" --dry-run\n"
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
            "patch, repair-admin, new-project, evolve-project, update, run, tui, doctor, web, task, "
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

    gr_p = sub_sub.add_parser("goal-report", help="Show tasks + prompt traces + artifacts for a goal")
    gr_p.add_argument("--goal-id", dest="goal_id", required=True)
    dr_p = sub_sub.add_parser("delegation-report", help="Show compact task delegation/template view for a goal")
    dr_p.add_argument("--goal-id", dest="goal_id", required=True)
    dr_p.add_argument("--json", action="store_true")
    tr_p = sub_sub.add_parser("task-report", help="Show compact prompt/response view for a task")
    tr_p.add_argument("--task-id", dest="task_id", required=True)
    tr_p.add_argument("--json", action="store_true")
    tt_p = sub_sub.add_parser("task-traces", help="Show all prompt traces for a task")
    tt_p.add_argument("--task-id", dest="task_id", required=True)
    tt_p.add_argument("--goal-id", dest="goal_id", default="")
    tt_p.add_argument("--propose-only", dest="propose_only", action="store_true")
    tt_p.add_argument("--json", action="store_true")
    ti_p = sub_sub.add_parser("task-inspect", help="Alias for task-report")
    ti_p.add_argument("--task-id", dest="task_id", required=True)
    ti_p.add_argument("--json", action="store_true")
    lr_p = sub_sub.add_parser("learning-report", help="Show planning learning loop snapshot")
    lr_p.add_argument("--json", action="store_true")
    ls_p = sub_sub.add_parser("learning-status", help="Show compact planning learning status")
    ls_p.add_argument("--json", action="store_true")
    pp_p = sub_sub.add_parser("planner-profiles", help="Show planning model profiles")
    pp_p.add_argument("--provider", default="")
    pp_p.add_argument("--model", default="")
    pp_p.add_argument("--json", action="store_true")
    gf_p = sub_sub.add_parser("goal-flows", help="Compact per-task flow view with executor/propose/artifacts")
    gf_p.add_argument("--goal-id", dest="goal_id", required=True)
    gf_p.add_argument("--json", action="store_true")
    tw_p = sub_sub.add_parser("task-why", help="Show latest completion/transition reason for a task")
    tw_p.add_argument("--task-id", dest="task_id", required=True)
    tw_p.add_argument("--json", action="store_true")
    gs_p = sub_sub.add_parser("goal-stuck", help="Show tasks likely stuck in proposing/assigned/in_progress")
    gs_p.add_argument("--goal-id", dest="goal_id", required=True)
    gs_p.add_argument("--minutes", type=int, default=10)
    gs_p.add_argument("--json", action="store_true")
    ge_p = sub_sub.add_parser("goal-execmap", help="Group tasks by inferred executor")
    ge_p.add_argument("--goal-id", dest="goal_id", required=True)
    ge_p.add_argument("--json", action="store_true")
    ap_p = sub_sub.add_parser("artifact-provenance", help="Show artifact provenance matrix for a goal")
    ap_p.add_argument("--goal-id", dest="goal_id", required=True)
    ap_p.add_argument("--json", action="store_true")
    ap_p.add_argument("--out", default="")
    ap_p.add_argument("--with-md", dest="with_md", action="store_true")
    ap_alias = sub_sub.add_parser("goal-artifact-matrix", help="Alias for artifact-provenance")
    ap_alias.add_argument("--goal-id", dest="goal_id", required=True)
    ap_alias.add_argument("--json", action="store_true")
    ap_alias.add_argument("--out", default="")
    ap_alias.add_argument("--with-md", dest="with_md", action="store_true")
    gwt_p = sub_sub.add_parser("goal-worker-traces", help="Fetch worker-side prompt traces for all tasks in a goal")
    gwt_p.add_argument("--goal-id", dest="goal_id", required=True)
    gwt_p.add_argument("--propose-only", dest="propose_only", action="store_true")
    gwt_p.add_argument("--full", action="store_true")
    gwt_p.add_argument("--limit", type=int, default=80)
    gwt_p.add_argument("--json", action="store_true")

    if not argv or argv[0] in ("-h", "--help"):
        sub_parser.print_help()
        return 0
    try:
        parsed = sub_parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    return run_prompt_command(parsed)


def _run_task(argv: Sequence[str]) -> int:
    from agent.cli.prompt_inspect import run_prompt_command
    sub_parser = argparse.ArgumentParser(prog="ananta task")
    sub_sub = sub_parser.add_subparsers(dest="task_cmd")

    inspect_p = sub_sub.add_parser("inspect", help="Inspect a task's prompt traces and latest response")
    inspect_p.add_argument("--task-id", dest="task_id", required=True)
    inspect_p.add_argument("--json", action="store_true")

    if not argv or argv[0] in ("-h", "--help"):
        sub_parser.print_help()
        return 0
    try:
        parsed = sub_parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    if getattr(parsed, "task_cmd", None) == "inspect":
        parsed.prompt_cmd = "task-inspect"
        return run_prompt_command(parsed)
    return 2


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
    normalized = list(argv)
    if "--goal-id" in normalized and "--goal-detail" not in normalized and "--goal-purge" not in normalized:
        idx = normalized.index("--goal-id")
        normalized[idx] = "--goal-detail"
    if argv[0] in GOAL_ALIAS_COMMANDS:
        return run_goal_alias(argv[0], argv[1:])
    return run_cli_goals(normalized)


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
    if command == "run":
        return _run_ananta_run(rest)
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
    if command == "task":
        return _run_task(rest)
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
