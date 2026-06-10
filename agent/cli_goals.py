#!/usr/bin/env python3
"""
CLI for goals, diagnostics and artifacts in Ananta.

Usage:
    ananta ask "Implement user authentication"
    ananta first-run
    ananta goal --goal "Add API endpoint" --context "Using Flask" --team dev
    ananta goal --goals
    ananta status

Module layout (SPLIT-013): this module is the facade and CLI entry point.
It keeps the hub HTTP/auth/output helpers (so tests can monkeypatch
agent.cli_goals attributes) and dispatches to:

  - agent/cli_goals_query.py     read-only commands (status, lists, detail)
  - agent/cli_goals_mutation.py  goal submission and destructive commands
  - agent/cli_goals_repair.py    repair-script flow (scan, poll, extract)
  - agent/cli_goals_planning.py  'sources' and 'plan summary' handlers
"""

import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests

from agent.config import settings
from agent.tui_contract import sanitize_terminal_text

SHORTCUT_GOALS = {
    "ask": {
        "mode": None,
        "prefix": "Beantworte diese Frage und nenne bei Unsicherheit die naechsten pruefbaren Schritte:",
        "context": "Kurzkommando: Frage. Fokus auf klare Antwort, Annahmen und naechste pruefbare Schritte.",
    },
    "plan": {
        "mode": None,
        "prefix": "Plane konkrete naechste Schritte fuer:",
        "context": "Kurzkommando: Planen. Fokus auf Ziel, Aufgaben, Reihenfolge und Pruefung.",
    },
    "analyze": {
        "mode": "repo_analysis",
        "prefix": "Analysiere und fasse die wichtigsten Befunde zusammen:",
        "context": "Kurzkommando: Analyse. Fokus auf Verstaendnis, Risiken und naechste Schritte.",
    },
    "review": {
        "mode": "code_review",
        "prefix": "Fuehre ein Review durch und priorisiere konkrete Risiken:",
        "context": "Kurzkommando: Review. Fokus auf Bugs, Regressionen, Tests und klare Findings.",
    },
    "diagnose": {
        "mode": "docker_compose_repair",
        "prefix": "Diagnostiziere das Problem und schlage eine robuste Start- oder Reparatursequenz vor:",
        "context": "Kurzkommando: Diagnose. Fokus auf Logs, Compose, Ports, Health-Checks und naechste Pruefung.",
    },
    "patch": {
        "mode": "code_fix",
        "prefix": "Plane einen kleinen, testbaren Patch fuer:",
        "context": "Kurzkommando: Patch. Fokus auf kleine Aenderung, Regressionstest und minimale Nebenwirkungen.",
    },
    "new-project": {
        "mode": "new_software_project",
        "prefix": "Lege ein neues Softwareprojekt kontrolliert an aus dieser Idee:",
        "context": "Kurzkommando: Neues Projekt. Fokus auf Scope, Architekturvorschlag, initiales Backlog, Tests und sichere Defaults.",
    },
    "evolve-project": {
        "mode": "project_evolution",
        "prefix": "Plane eine kontrollierte Weiterentwicklung fuer ein bestehendes Projekt:",
        "context": "Kurzkommando: Projekt weiterentwickeln. Fokus auf betroffene Bereiche, Risiken, Tests und kleine reviewbare Schritte.",
    },
    "repair-admin": {
        "mode": "admin_repair",
        "prefix": "Plane eine bounded Admin-Reparatur als Shared Foundation fuer:",
        "context": "Kurzkommando: Admin Repair. Fokus auf bounded evidence, dry-run-first, advisory Klassifikation und verifizierbare Repair-Schritte.",
    },
}


def get_base_url():
    configured = os.environ.get("ANANTA_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{settings.port}"


_CONTAINER_WORKSPACE_ROOT = "/project-workspaces"
_HOST_WORKSPACE_ROOT = "./project-workspaces"


def _resolve_output_dir(output_dir: str) -> tuple[str, str | None]:
    """Translate a user-supplied output_dir to (container_path, host_display_path).

    Rules:
    - Bare name or relative path  → /project-workspaces/<name>
    - Absolute /project-workspaces/…  → kept as-is
    - Other absolute path  → kept as-is, host_display_path is None
    """
    raw = output_dir.strip()
    if not raw:
        return raw, None
    if raw.startswith(_CONTAINER_WORKSPACE_ROOT + "/") or raw == _CONTAINER_WORKSPACE_ROOT:
        rel = raw[len(_CONTAINER_WORKSPACE_ROOT):].lstrip("/")
        host = f"{_HOST_WORKSPACE_ROOT}/{rel}" if rel else _HOST_WORKSPACE_ROOT
        return raw, host
    if not os.path.isabs(raw):
        name = raw.removeprefix("./").replace("\\", "/").strip("/") or raw
        container = f"{_CONTAINER_WORKSPACE_ROOT}/{name}"
        host = f"{_HOST_WORKSPACE_ROOT}/{name}"
        return container, host
    # Arbitrary absolute host path: map to shared /project-workspaces and mirror via symlink.
    host_requested = Path(raw)
    host_ws_root = Path(_HOST_WORKSPACE_ROOT).resolve()
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip("/")).strip("-.") or "workspace"
    container = f"{_CONTAINER_WORKSPACE_ROOT}/external/{slug}"
    host_backing = host_ws_root / "external" / slug
    try:
        host_backing.mkdir(parents=True, exist_ok=True)
        host_requested.parent.mkdir(parents=True, exist_ok=True)
        if host_requested.exists() or host_requested.is_symlink():
            if host_requested.is_symlink():
                current_target = host_requested.resolve(strict=False)
                if current_target != host_backing.resolve():
                    return container, str(host_backing)
            elif host_requested.is_dir():
                # Keep existing real directories untouched to avoid destructive behavior.
                return container, str(host_requested)
            else:
                return container, str(host_backing)
        else:
            host_requested.symlink_to(host_backing, target_is_directory=True)
    except Exception:
        return container, str(host_backing)
    return container, str(host_requested)


def _parse_rag_sources(raw: str) -> dict:
    """Parse comma-separated RAG source tokens into a rag_sources dict.

    Token formats:
      col:<id>  or bare <id>  → knowledge_collection_ids
      art:<id>                → artifact_ids
      path:<rel-path>         → repo_scope_refs
    """
    if not raw:
        return {}
    collection_ids: list[str] = []
    artifact_ids: list[str] = []
    repo_scope_refs: list[dict] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.startswith("art:"):
            artifact_ids.append(token[4:].strip())
        elif token.startswith("path:"):
            repo_scope_refs.append({"path": token[5:].strip()})
        else:
            collection_ids.append(token.removeprefix("col:").strip())
    result: dict = {}
    if collection_ids:
        result["knowledge_collection_ids"] = collection_ids
    if artifact_ids:
        result["artifact_ids"] = artifact_ids
    if repo_scope_refs:
        result["repo_scope_refs"] = repo_scope_refs
    return result


def get_auth_token(base_url: str) -> str:
    username = (
        os.environ.get("ANANTA_USER")
        or os.environ.get("INITIAL_ADMIN_USER")
        or "admin"
    )
    password = (
        os.environ.get("ANANTA_PASSWORD")
        or os.environ.get("INITIAL_ADMIN_PASSWORD")
        or "admin"
    )

    try:
        response = requests.post(f"{base_url}/login", json={"username": username, "password": password}, timeout=10)
    except requests.RequestException as exc:
        _print_terminal("Error: Hub not reachable at {}", base_url)
        _print_terminal("Next step: start the hub or set ANANTA_BASE_URL. Details: {}", str(exc))
        sys.exit(1)

    if response.status_code != 200:
        _print_terminal("Error: Login failed - {}", response.status_code)
        print("Next step: check ANANTA_USER/ANANTA_PASSWORD or reset the local admin password.")
        sys.exit(1)

    data = response.json().get("data", {})
    return data.get("access_token", "")


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
):
    base_url = get_base_url()
    token = get_auth_token(base_url)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        return requests.request(
            method=method,
            url=f"{base_url}{path}",
            headers=headers,
            json=body,
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        _print_terminal("Error: Hub request failed for {}", path)
        _print_terminal("Next step: run `ananta first-run` and verify ANANTA_BASE_URL. Details: {}", str(exc))
        sys.exit(1)


def _read_json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}


def _api_data(response: requests.Response):
    payload = _read_json(response)
    if isinstance(payload, dict):
        return payload.get("data", payload)
    return {}


def _terminal(value, *, max_chars: int = 240) -> str:
    return sanitize_terminal_text(value, max_chars=max_chars)


def _print_terminal(template: str, *values) -> None:
    print(template.format(*(_terminal(value) for value in values)))


def _print_error(response: requests.Response):
    payload = _read_json(response)
    message = payload.get("message") if isinstance(payload, dict) else None
    if message:
        _print_terminal("Error: {} - {}", response.status_code, message)
    else:
        _print_terminal("Error: {} - {}", response.status_code, response.text)
    _print_terminal("Next step: {}", _next_step_for_status(response.status_code, message or response.text))


def _next_step_for_status(status_code: int, message: str | None = None) -> str:
    text = str(message or "").lower()
    if status_code in {401, 403}:
        return "check ANANTA_USER/ANANTA_PASSWORD and governance permissions."
    if status_code == 404:
        return "check that the hub version exposes this endpoint and that ANANTA_BASE_URL points to the hub."
    if status_code == 409 or "policy" in text or "governance" in text or "blocked" in text:
        return "review the governance mode or narrow the goal before retrying."
    if status_code >= 500:
        return "check hub logs, then run `ananta status` after the hub is healthy."
    return "retry with a narrower goal or run `ananta status` for readiness."


def _planning_mode_to_use_template(planning_mode: str | None) -> bool | None:
    """Map --planning-mode flag to use_template API field."""
    if not planning_mode:
        return None
    m = planning_mode.strip().lower()
    if m == "llm":
        return False
    if m in {"template", "auto"}:
        return True
    return None


def _parse_mode_data(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON for --mode-data ({exc})")
        sys.exit(2)
    if not isinstance(parsed, dict):
        print("Error: --mode-data must be a JSON object")
        sys.exit(2)
    return parsed


# Command implementations live in the sub-modules; they are re-exported here
# so existing imports and monkeypatch targets on agent.cli_goals keep working.
# These imports must stay below the helper definitions above (the sub-modules
# import this module as a facade).
#
# When executed via `python -m agent.cli_goals`, this file runs as __main__;
# register it as agent.cli_goals first so the sub-modules bind to this same
# instance instead of re-importing the file (which would deadlock the cycle).
if __name__ == "__main__":
    sys.modules.setdefault("agent.cli_goals", sys.modules[__name__])
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


def main(argv: list[str] | None = None):
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

    args = parser.parse_args(argv)

    if args.first_run:
        show_first_run()
        return

    if args.config_show or args.set_runtime_profile or args.set_governance_mode:
        patch = {}
        if args.set_runtime_profile:
            patch["runtime_profile"] = str(args.set_runtime_profile).strip()
        if args.set_governance_mode:
            patch["governance_mode"] = str(args.set_governance_mode).strip()
        if patch:
            res = _request("POST", "/config", body=patch, timeout=10)
            if res.status_code != 200:
                _print_error(res)
                sys.exit(1)
        res = _request("GET", "/config", timeout=10)
        if res.status_code != 200:
            _print_error(res)
            sys.exit(1)
        cfg = _api_data(res) or {}
        runtime = (cfg.get("runtime_profile_effective") or {}).get("effective") or cfg.get("runtime_profile") or "-"
        governance = (cfg.get("governance_mode_effective") or {}).get("effective") or cfg.get("governance_mode") or "-"
        _print_terminal("runtime_profile: {}", runtime)
        _print_terminal("governance_mode: {}", governance)
        return

    if args.planning_stuck:
        sys.exit(planning_stuck())
    elif args.recover_stale:
        sys.exit(recover_stale(dry_run=not args.yes))
    elif args.cancel_tree:
        if not args.yes:
            print("Error: --cancel-tree is destructive and requires --yes")
            sys.exit(2)
        sys.exit(cancel_tree(args.cancel_tree))
    elif args.kill_requests:
        sys.exit(kill_requests(args.kill_requests))
    elif args.kill_all_requests:
        sys.exit(kill_all_requests())
    elif args.status:
        show_status()
    elif args.goals:
        list_goals(limit=args.limit)
    elif args.goal_purge:
        if not args.yes:
            print("Error: --goal-purge is destructive and requires --yes")
            sys.exit(2)
        rc = purge_goal(args.goal_purge)
        if rc != 0:
            sys.exit(rc)
    elif args.goal_detail:
        show_goal_detail(args.goal_detail)
    elif args.goal_tasks:
        list_goal_tasks(args.goal_tasks)
    elif args.modes:
        list_modes()
    elif args.tasks:
        list_tasks(status=args.task_status, limit=args.limit)
    elif args.artifacts:
        list_artifacts(limit=args.limit)
    elif args.analyze_task:
        analyze_task_followups(args.analyze_task, output=args.output)
    elif args.goal == "sources":
        subcommand = str(args.extra[0]).strip() if args.extra else ""
        if not subcommand:
            print("Error: 'sources' requires a subcommand (list-packs|bootstrap|doctor|query)", file=sys.stderr)
            sys.exit(2)
        sys.exit(_handle_sources_command(subcommand, args.extra[1:], args))
    elif args.goal == "plan" and args.extra and str(args.extra[0]).strip().lower() == "summary":
        sys.exit(_handle_plan_command("summary", args.extra[1:], args))
    elif args.goal == "repair-script":
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
        )
    elif args.goal in SHORTCUT_GOALS:
        shortcut_text = " ".join(args.extra).strip()
        if not shortcut_text:
            print(f"Error: '{args.goal}' needs a short description")
            sys.exit(2)
        output_dir = args.output_dir.strip() if args.output_dir else None
        rag_sources = getattr(args, "rag_sources", None)
        shortcut_kwargs = {"team_id": args.team, "create_tasks": not args.no_create}
        if output_dir is not None:
            shortcut_kwargs["output_dir"] = output_dir
        if args.planning_mode is not None:
            shortcut_kwargs["planning_mode"] = args.planning_mode
        if rag_sources is not None:
            shortcut_kwargs["rag_sources"] = rag_sources
        submit_shortcut(args.goal, shortcut_text, **shortcut_kwargs)
    elif args.goal or args.goal_flag:
        goal_text = args.goal or args.goal_flag
        if args.extra:
            goal_text = " ".join([goal_text, *args.extra])
        create_tasks = not args.no_create
        output_dir = args.output_dir.strip() if args.output_dir else None
        rag_sources = getattr(args, "rag_sources", None)
        submit_goal(
            goal=goal_text,
            context=args.context,
            team_id=args.team,
            create_tasks=create_tasks,
            mode=args.mode,
            mode_data=_parse_mode_data(args.mode_data),
            output_dir=output_dir,
            planning_mode=args.planning_mode,
            rag_sources=rag_sources,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
