#!/usr/bin/env python3
"""
CLI for goals, diagnostics and artifacts in Ananta.

Usage:
    ananta ask "Implement user authentication"
    ananta first-run
    ananta goal --goal "Add API endpoint" --context "Using Flask" --team dev
    ananta goal --goals
    ananta status

Module layout (SPLIT-013): this module is the CLI entry point and a
backward-compatible re-export facade. Collaborators are injected explicitly
through :class:`agent.cli_goals_support.CliGoalsDependencies` (``deps``):

  - agent/cli_goals_support.py   hub HTTP/auth, terminal output, parsing, deps
  - agent/cli_goals_query.py     read-only commands (status, lists, detail)
  - agent/cli_goals_mutation.py  goal submission and destructive commands
  - agent/cli_goals_repair.py    repair-script flow (scan, poll, extract)
  - agent/cli_goals_planning.py  'sources' and 'plan summary' handlers
"""

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests  # noqa: E402,F401  (re-exported for backward compatibility)

from agent.cli_goals_mutation import (  # noqa: E402
    _shortcut_mode_data,
    analyze_task_followups,
    cancel_tree,
    kill_all_requests,
    kill_requests,
    purge_goal,
    recover_stale,
    submit_goal,
    submit_shortcut,
)
from agent.cli_goals_planning import (  # noqa: E402
    _handle_plan_command,
    _handle_sources_command,
)
from agent.cli_goals_query import (  # noqa: E402
    list_artifacts,
    list_goal_tasks,
    list_goals,
    list_modes,
    list_tasks,
    planning_stuck,
    show_first_run,
    show_goal_detail,
    show_status,
)
from agent.cli_goals_repair import (  # noqa: E402
    _REPAIR_SCRIPT_CFG,
    _TERMINAL_GOAL_STATUSES,
    _extract_script_blocks,
    _fetch_goal_outputs,
    _fetch_task_full_output,
    _host_scan,
    _poll_goal_status,
    _run_scan_cmd,
    _submit_repair_goal,
    _switch_autopilot_to_goal,
    repair_script_cmd,
)
from agent.cli_goals_support import (  # noqa: E402
    DEFAULT_DEPENDENCIES,
    SHORTCUT_GOALS,
    CliGoalsDependencies,
    HubHttpClient,
    get_auth_token,
    get_base_url,
    hub_request,
)
from agent.cli_goals_support import api_data as _api_data  # noqa: E402
from agent.cli_goals_support import next_step_for_status as _next_step_for_status  # noqa: E402
from agent.cli_goals_support import parse_mode_data as _parse_mode_data  # noqa: E402
from agent.cli_goals_support import parse_rag_sources as _parse_rag_sources  # noqa: E402
from agent.cli_goals_support import planning_mode_to_use_template as _planning_mode_to_use_template  # noqa: E402
from agent.cli_goals_support import print_error as _print_error  # noqa: E402
from agent.cli_goals_support import print_terminal as _print_terminal  # noqa: E402
from agent.cli_goals_support import read_json as _read_json  # noqa: E402
from agent.cli_goals_support import resolve_output_dir as _resolve_output_dir  # noqa: E402
from agent.cli_goals_support import terminal_text as _terminal  # noqa: E402

# Backward-compatible name of the default authenticated hub request adapter.
_request = hub_request

__all__ = [
    "DEFAULT_DEPENDENCIES",
    "SHORTCUT_GOALS",
    "CliGoalsDependencies",
    "HubHttpClient",
    "_REPAIR_SCRIPT_CFG",
    "_TERMINAL_GOAL_STATUSES",
    "_api_data",
    "_extract_script_blocks",
    "_fetch_goal_outputs",
    "_fetch_task_full_output",
    "_handle_plan_command",
    "_handle_sources_command",
    "_host_scan",
    "_next_step_for_status",
    "_parse_mode_data",
    "_parse_rag_sources",
    "_planning_mode_to_use_template",
    "_poll_goal_status",
    "_print_error",
    "_print_terminal",
    "_read_json",
    "_request",
    "_resolve_output_dir",
    "_run_scan_cmd",
    "_shortcut_mode_data",
    "_submit_repair_goal",
    "_switch_autopilot_to_goal",
    "_terminal",
    "analyze_task_followups",
    "build_parser",
    "cancel_tree",
    "get_auth_token",
    "get_base_url",
    "kill_all_requests",
    "kill_requests",
    "list_artifacts",
    "list_goal_tasks",
    "list_goals",
    "list_modes",
    "list_tasks",
    "main",
    "planning_stuck",
    "purge_goal",
    "recover_stale",
    "repair_script_cmd",
    "show_first_run",
    "show_goal_detail",
    "show_status",
    "submit_goal",
    "submit_shortcut",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI for Ananta Goals, Tasks, Artifacts and Diagnostics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First run:
    ananta first-run
    ananta status
    ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"

  Golden path (PRD-021): Short human-friendly commands:
    ananta ask "What should I do next?"
    ananta plan "Prepare a release checklist"
    ananta analyze "Find the riskiest frontend areas"
    ananta review "Review the auth changes"
    ananta diagnose "Docker frontend cannot reach hub"
    ananta patch "Fix failing login validation"
    ananta new-project "Build a small release-check tool for maintainers"
    ananta evolve-project "Add a guided project-start mode to the dashboard"
    ananta repair-admin "Service restart loop after update"

  Write output to a specific folder (files appear under ./project-workspaces/ on the host):
    ananta new-project --output-dir myproject "Build a small tool"
    ananta ask --output-dir fibonacci "Write a fibonacci.py"
    ananta ask --output-dir ./out "Generate a README for this repo"
    (Relative names are mapped to /project-workspaces/<name> inside the worker container.)

  Repair-script (synchronous, pipe-friendly):
    ananta repair-script "Nginx crashes on startup"
    ananta repair-script "Nginx crashes" > fix.sh && cat fix.sh
    ananta repair-script "Nginx crashes" | bash
    ananta repair-script "Nginx crashes" --script-out fix.sh
    ananta repair-script "Nginx crashes" --exec
    ananta repair-script "Nginx crashes" --tui          # interactive TUI: approve/run on host
    ananta repair-script "Nginx crashes" --loop         # TUI + automatische Retry-Schleife
    ananta repair-script "Nginx crashes" --loop --max-iterations 5
    ananta repair-script "Nginx crashes" --wait-timeout 120

  Planning strategy (default: llm — KI-gestützt):
    ananta ask "What next?" --planning-mode llm      # KI-Planung (Standard)
    ananta ask "What next?" --planning-mode template  # Template-Planung
    ananta repair-script "Nginx" --planning-mode llm
    ananta goal --goal "..." --planning-mode llm

  Profile/Governance (GOV-051/PRF-080):
    ananta goal --config-show
    ananta goal --set-runtime-profile demo --set-governance-mode safe

  Submit guided mode:
    ananta goal --goal "Container restart-loop" --mode docker_compose_repair --mode-data '{"service":"hub"}'

  List tasks:
    ananta goal --tasks

  List goals:
    ananta goal --goals

  Goal detail:
    ananta goal --goal-detail <goal_id>

  List guided modes:
    ananta goal --modes

  Analyze follow-ups for a task:
    ananta goal --analyze-task <task_id>

  Check status:
    ananta status
""",
    )

    parser.add_argument(
        "goal",
        nargs="?",
        help="Goal description to submit, or shortcut: ask/plan/analyze/review/diagnose/patch/new-project/evolve-project/repair-admin/repair-script/sources",
    )
    parser.add_argument("extra", nargs="*", help="Additional words for shortcut goals")
    parser.add_argument("--goal", "-g", dest="goal_flag", help="Goal description (alternative)")
    parser.add_argument("--context", "-c", help="Additional context for the goal")
    parser.add_argument("--team", "-t", help="Team ID to assign tasks to")
    parser.add_argument("--mode", help="Guided goal mode ID (e.g. code_fix, docker_compose_repair)")
    parser.add_argument("--mode-data", help='JSON object for mode fields, e.g. \'{"service":"hub"}\'')
    parser.add_argument("--output-dir", "-o", help="Directory where generated files are written. Relative names (e.g. 'fibonacci') map to /project-workspaces/<name> in the container and ./project-workspaces/<name> on the host.")
    parser.add_argument("--rag-sources", "-R", help="Comma-separated knowledge sources to attach (col:<id>, art:<id>, path:<rel>). Included as research context for every task in the goal.")
    parser.add_argument("--script-out", "-S", metavar="FILE", help="Save the extracted repair script to this file (repair-script only)")
    parser.add_argument("--exec", dest="exec_script", action="store_true", help="Review then optionally execute the generated script (repair-script only)")
    parser.add_argument("--tui", dest="tui_flag", action="store_true", help="Interactive TUI: review and approve commands for controlled host execution (repair-script only)")
    parser.add_argument("--loop", dest="loop_flag", action="store_true", help="TUI loop: plan → approve → execute → test → retry until fixed (repair-script only)")
    parser.add_argument("--max-iterations", type=int, default=3, metavar="N", help="Maximum loop iterations for --loop (default 3)")
    parser.add_argument("--scan", dest="scan_flag", action="store_true", help="Host-Diagnose vor LLM-Submission: sammelt Systemzustand lokal (repair-script only)")
    parser.add_argument("--wait-timeout", type=int, default=300, metavar="SECONDS", help="Max seconds to wait for goal completion, default 300 (repair-script only)")
    parser.add_argument("--no-create", action="store_true", help="Don't create tasks, just analyze")
    parser.add_argument("--status", "-s", action="store_true", help="Show Goal readiness + Auto-Planner status")
    parser.add_argument("--first-run", action="store_true", help="Show the official first CLI path, success signals and failure help")
    parser.add_argument("--goals", action="store_true", help="List goals")
    parser.add_argument("--goal-detail", help="Show detail for a goal ID")
    parser.add_argument("--goal-tasks", help="List all tasks for a goal ID")
    parser.add_argument("--goal-purge", help="Purge one goal and all related records (admin only)")
    parser.add_argument("--yes", action="store_true", help="Required confirmation for destructive actions like --goal-purge")
    parser.add_argument("--modes", action="store_true", help="List guided goal modes")
    parser.add_argument("--tasks", action="store_true", help="List recent tasks")
    parser.add_argument("--task-status", help="Filter tasks by status")
    parser.add_argument("--artifacts", action="store_true", help="List recent artifacts")
    parser.add_argument("--analyze-task", help="Analyze a completed task for follow-up work")
    parser.add_argument("--output", help="Optional output text for --analyze-task")
    parser.add_argument("--limit", "-l", type=int, default=20, help="Limit number of results")
    parser.add_argument("--config-show", action="store_true", help="Show effective runtime_profile + governance_mode")
    parser.add_argument("--set-runtime-profile", default="", help="Update runtime_profile via POST /config")
    parser.add_argument("--set-governance-mode", default="", help="Update governance_mode via POST /config")
    parser.add_argument(
        "--planning-mode",
        choices=["llm", "template", "auto"],
        default=None,
        metavar="MODE",
        help="Planning strategy: llm (default), template, auto. Overrides server-side default.",
    )
    # PRI-013: planning diagnostics
    parser.add_argument("--planning-stuck", action="store_true", help="List goals stuck in planning_running/queued with expired lease")
    parser.add_argument("--recover-stale", action="store_true", help="Cancel stale planning goals with expired lease (use --yes to execute, default: dry-run)")
    parser.add_argument("--cancel-tree", metavar="GOAL_ID", help="Cancel all tasks for a goal and mark it failed (admin, requires --yes)")
    parser.add_argument("--kill-requests", metavar="GOAL_ID", help="Abort all in-flight LM Studio requests for a goal (admin)")
    parser.add_argument("--kill-all-requests", action="store_true", help="Abort all in-flight LM Studio requests across all goals (admin)")
    parser.add_argument("--dry-run", action="store_true", help="Preview operation without writing state (sources bootstrap)")
    parser.add_argument("--skip-source", action="append", default=[], help="Source ID to skip (repeatable, sources bootstrap)")
    parser.add_argument("--include-optional-sources", action="store_true", help="Include optional sources in source-pack bootstrap")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Emit JSON output (sources doctor)")
    parser.add_argument("--write", action="store_true", help="Write changes for plan summary fix/migrate")
    parser.add_argument("--convert-epics", action="store_true", help="Convert legacy epics.tasks in plan summary migrate")
    return parser


# ── command dispatch ─────────────────────────────────────────────────────────


def _require_yes(args, flag: str) -> None:
    if not args.yes:
        print(f"Error: {flag} is destructive and requires --yes")
        sys.exit(2)


def _run_config_command(args, deps: CliGoalsDependencies) -> None:
    patch = {}
    if args.set_runtime_profile:
        patch["runtime_profile"] = str(args.set_runtime_profile).strip()
    if args.set_governance_mode:
        patch["governance_mode"] = str(args.set_governance_mode).strip()
    if patch:
        res = deps.request("POST", "/config", body=patch, timeout=10)
        if res.status_code != 200:
            _print_error(res)
            sys.exit(1)
    res = deps.request("GET", "/config", timeout=10)
    if res.status_code != 200:
        _print_error(res)
        sys.exit(1)
    cfg = deps.api_data(res) or {}
    runtime = (cfg.get("runtime_profile_effective") or {}).get("effective") or cfg.get("runtime_profile") or "-"
    governance = (cfg.get("governance_mode_effective") or {}).get("effective") or cfg.get("governance_mode") or "-"
    _print_terminal("runtime_profile: {}", runtime)
    _print_terminal("governance_mode: {}", governance)


def _run_cancel_tree(args, deps: CliGoalsDependencies) -> None:
    _require_yes(args, "--cancel-tree")
    sys.exit(cancel_tree(args.cancel_tree, deps=deps))


def _run_goal_purge(args, deps: CliGoalsDependencies) -> None:
    _require_yes(args, "--goal-purge")
    rc = purge_goal(args.goal_purge, deps=deps)
    if rc != 0:
        sys.exit(rc)


def _run_sources(args, deps: CliGoalsDependencies) -> None:
    subcommand = str(args.extra[0]).strip() if args.extra else ""
    if not subcommand:
        print("Error: 'sources' requires a subcommand (list-packs|bootstrap|doctor|query)", file=sys.stderr)
        sys.exit(2)
    sys.exit(_handle_sources_command(subcommand, args.extra[1:], args))


def _is_plan_summary(args) -> bool:
    return args.goal == "plan" and bool(args.extra) and str(args.extra[0]).strip().lower() == "summary"


def _run_repair_script(args, deps: CliGoalsDependencies) -> None:
    shortcut_text = " ".join(args.extra).strip()
    if not shortcut_text:
        print("Error: 'repair-script' needs a short description", file=sys.stderr)
        sys.exit(2)
    repair_script_cmd(
        shortcut_text,
        team_id=args.team,
        script_out=args.script_out,
        exec_flag=args.exec_script,
        tui_flag=args.tui_flag,
        loop_flag=args.loop_flag,
        scan=args.scan_flag,
        max_iterations=args.max_iterations,
        timeout=args.wait_timeout,
        planning_mode=args.planning_mode,
        deps=deps,
    )


def _optional_output_dir(args) -> str | None:
    return args.output_dir.strip() if args.output_dir else None


def _run_shortcut(args, deps: CliGoalsDependencies) -> None:
    shortcut_text = " ".join(args.extra).strip()
    if not shortcut_text:
        print(f"Error: '{args.goal}' needs a short description")
        sys.exit(2)
    output_dir = _optional_output_dir(args)
    rag_sources = getattr(args, "rag_sources", None)
    shortcut_kwargs = {"team_id": args.team, "create_tasks": not args.no_create}
    if output_dir is not None:
        shortcut_kwargs["output_dir"] = output_dir
    if args.planning_mode is not None:
        shortcut_kwargs["planning_mode"] = args.planning_mode
    if rag_sources is not None:
        shortcut_kwargs["rag_sources"] = rag_sources
    submit_shortcut(args.goal, shortcut_text, **shortcut_kwargs, deps=deps)


def _run_submit_goal(args, deps: CliGoalsDependencies) -> None:
    goal_text = args.goal or args.goal_flag
    if args.extra:
        goal_text = " ".join([goal_text, *args.extra])
    submit_goal(
        goal=goal_text,
        context=args.context,
        team_id=args.team,
        create_tasks=not args.no_create,
        mode=args.mode,
        mode_data=_parse_mode_data(args.mode_data),
        output_dir=_optional_output_dir(args),
        planning_mode=args.planning_mode,
        rag_sources=getattr(args, "rag_sources", None),
        deps=deps,
    )


@dataclass(frozen=True)
class _CliCommand:
    """One entry of the ordered dispatch table: the first matching command wins."""

    matches: Callable[[argparse.Namespace], bool]
    run: Callable[[argparse.Namespace, CliGoalsDependencies], None]


_COMMANDS: tuple[_CliCommand, ...] = (
    _CliCommand(lambda a: a.first_run, lambda a, d: show_first_run(deps=d)),
    _CliCommand(
        lambda a: bool(a.config_show or a.set_runtime_profile or a.set_governance_mode),
        _run_config_command,
    ),
    _CliCommand(lambda a: a.planning_stuck, lambda a, d: sys.exit(planning_stuck(deps=d))),
    _CliCommand(lambda a: a.recover_stale, lambda a, d: sys.exit(recover_stale(dry_run=not a.yes, deps=d))),
    _CliCommand(lambda a: a.cancel_tree, _run_cancel_tree),
    _CliCommand(lambda a: a.kill_requests, lambda a, d: sys.exit(kill_requests(a.kill_requests, deps=d))),
    _CliCommand(lambda a: a.kill_all_requests, lambda a, d: sys.exit(kill_all_requests(deps=d))),
    _CliCommand(lambda a: a.status, lambda a, d: show_status(deps=d)),
    _CliCommand(lambda a: a.goals, lambda a, d: list_goals(limit=a.limit, deps=d)),
    _CliCommand(lambda a: a.goal_purge, _run_goal_purge),
    _CliCommand(lambda a: a.goal_detail, lambda a, d: show_goal_detail(a.goal_detail, deps=d)),
    _CliCommand(lambda a: a.goal_tasks, lambda a, d: list_goal_tasks(a.goal_tasks, deps=d)),
    _CliCommand(lambda a: a.modes, lambda a, d: list_modes(deps=d)),
    _CliCommand(lambda a: a.tasks, lambda a, d: list_tasks(status=a.task_status, limit=a.limit, deps=d)),
    _CliCommand(lambda a: a.artifacts, lambda a, d: list_artifacts(limit=a.limit, deps=d)),
    _CliCommand(
        lambda a: a.analyze_task,
        lambda a, d: analyze_task_followups(a.analyze_task, output=a.output, deps=d),
    ),
    _CliCommand(lambda a: a.goal == "sources", _run_sources),
    _CliCommand(_is_plan_summary, lambda a, d: sys.exit(_handle_plan_command("summary", a.extra[1:], a))),
    _CliCommand(lambda a: a.goal == "repair-script", _run_repair_script),
    _CliCommand(lambda a: a.goal in SHORTCUT_GOALS, _run_shortcut),
    _CliCommand(lambda a: bool(a.goal or a.goal_flag), _run_submit_goal),
)


def main(argv: list[str] | None = None, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    parser = build_parser()
    args = parser.parse_args(argv)
    for command in _COMMANDS:
        if command.matches(args):
            command.run(args, deps)
            return None
    parser.print_help()
    return None


if __name__ == "__main__":
    main()
