#!/usr/bin/env python3
"""
CLI for goals, diagnostics and artifacts in Ananta.

Usage:
    python -m agent.cli_goals "Implement user authentication"
    python -m agent.cli_goals --first-run
    python -m agent.cli_goals --goal "Add API endpoint" --context "Using Flask" --team dev
    python -m agent.cli_goals --goals
    python -m agent.cli_goals --status
"""

import argparse
import json
import os
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


def get_auth_token(base_url: str) -> str:
    username = os.environ.get("ANANTA_USER", "admin")
    password = os.environ.get("ANANTA_PASSWORD", "admin")

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
        _print_terminal("Next step: run `python -m agent.cli_goals --first-run` and verify ANANTA_BASE_URL. Details: {}", str(exc))
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
        return "check hub logs, then run `python -m agent.cli_goals --status` after the hub is healthy."
    return "retry with a narrower goal or run `python -m agent.cli_goals --status` for readiness."


def show_first_run():
    base_url = get_base_url()
    print("Ananta CLI First Run")
    print("=====================")
    _print_terminal("Hub URL: {}", base_url)
    print("\n1. Optional environment:")
    _print_terminal("   export ANANTA_BASE_URL={}", base_url)
    print("   export ANANTA_USER=admin")
    print("   export ANANTA_PASSWORD=<password>")
    print("\n2. Readiness check:")
    print("   python -m agent.cli_goals --status")
    print("\n3. Official first goal:")
    print('   python -m agent.cli_goals plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"')
    print("\nSuccess signal:")
    print("   - Goal ID is printed")
    print("   - Status is printed")
    print("   - Tasks created is greater than 0 for a planned goal")
    print("\nAfter success:")
    print("   python -m agent.cli_goals --tasks --task-status todo")
    print("   python -m agent.cli_goals --goal-detail <goal_id>")
    print("\nIf it fails:")
    print("   - login error: check ANANTA_USER/ANANTA_PASSWORD")
    print("   - connection error: start the hub or set ANANTA_BASE_URL")
    print("   - governance/policy block: narrow the goal or inspect the governance mode")


def submit_goal(
    goal: str,
    context: str | None = None,
    team_id: str | None = None,
    create_tasks: bool = True,
    mode: str | None = None,
    mode_data: dict | None = None,
):
    payload = {"goal": goal, "create_tasks": create_tasks}
    if context:
        payload["context"] = context
    if team_id:
        payload["team_id"] = team_id
    if mode:
        payload["mode"] = mode
    if mode_data:
        payload["mode_data"] = mode_data

    response = _request("POST", "/goals", body=payload, timeout=60)
    if response.status_code == 201:
        data = _api_data(response)
        goal_payload = data.get("goal", {})
        created_task_ids = data.get("created_task_ids", [])
        _print_terminal("Goal submitted: {}", goal_payload.get("goal", goal))
        _print_terminal("Goal ID: {}", goal_payload.get("id", "N/A"))
        _print_terminal("Status: {}", goal_payload.get("status", "N/A"))
        print(f"Tasks created: {len(created_task_ids)}")
        for task_id in created_task_ids:
            _print_terminal("  - {}", task_id)
        reference_profile = dict(goal_payload.get("reference_profile") or {})
        if reference_profile:
            _print_terminal("Reference profile: {}", reference_profile.get("profile_id") or "-")
            _print_terminal("Reference fit: {}", reference_profile.get("fit_level") or "n/a")
            if reference_profile.get("reason_summary"):
                _print_terminal("Reference reason: {}", reference_profile.get("reason_summary"))
        goal_id = goal_payload.get("id")
        if goal_id:
            _print_terminal("Next step: python -m agent.cli_goals --goal-detail {}", goal_id)
        print("Success signal: Goal ID, status and task count are visible.")
        return created_task_ids
    _print_error(response)
    return []


def submit_shortcut(kind: str, text: str, *, team_id: str | None = None, create_tasks: bool = True):
    shortcut = SHORTCUT_GOALS.get(kind)
    if not shortcut:
        _print_terminal("Error: Unknown shortcut '{}'. Available: {}", kind, ", ".join(sorted(SHORTCUT_GOALS)))
        return []
    shortcut_text = text.strip()
    return submit_goal(
        goal=f"{shortcut['prefix']} {shortcut_text}",
        context=shortcut["context"],
        team_id=team_id,
        create_tasks=create_tasks,
        mode=shortcut.get("mode"),
        mode_data=_shortcut_mode_data(kind, shortcut_text),
    )


def _shortcut_mode_data(kind: str, text: str) -> dict:
    data = {"shortcut": kind, "shortcut_text": text}
    if kind == "patch":
        data["issue_description"] = text
    elif kind == "review":
        data["scope"] = text
    elif kind == "diagnose":
        data["issue_symptom"] = text
    elif kind == "analyze":
        data["scope"] = text
    elif kind == "new-project":
        data["project_idea"] = text
    elif kind == "evolve-project":
        data["change_goal"] = text
    elif kind == "repair-admin":
        data["issue_symptom"] = text
        data["platform_target"] = "auto"
        data["execution_scope"] = "bounded_repair"
        data["dry_run"] = True
    return data


def show_status():
    readiness_res = _request("GET", "/goals/readiness", timeout=10)
    if readiness_res.status_code == 200:
        readiness = _api_data(readiness_res)
        print("Goal Readiness:")
        print(f"  Happy path ready: {readiness.get('happy_path_ready', False)}")
        print(f"  Planning available: {readiness.get('planning_available', False)}")
        print(f"  Worker available: {readiness.get('worker_available', False)}")
        _print_terminal("  Active team: {}", readiness.get("active_team_id") or "-")
    else:
        _print_error(readiness_res)

    planner_res = _request("GET", "/tasks/auto-planner/status", timeout=10)
    if planner_res.status_code == 200:
        data = _api_data(planner_res)
        stats = data.get("stats", {})
        print("\nAuto-Planner Status:")
        print(f"  Enabled: {data.get('enabled', False)}")
        print(f"  Goals processed: {stats.get('goals_processed', 0)}")
        print(f"  Tasks created: {stats.get('tasks_created', 0)}")
        print(f"  Follow-ups created: {stats.get('followups_created', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")
    else:
        _print_error(planner_res)


def list_tasks(status: str = None, limit: int = 20):
    params = {"limit": limit}
    if status:
        params["status"] = status

    response = _request("GET", "/tasks", params=params, timeout=10)

    if response.status_code == 200:
        tasks = _read_json(response)
        if isinstance(tasks, dict):
            tasks = tasks.get("data", [])

        print(f"Tasks ({len(tasks)}):")
        for task in tasks[:limit]:
            task_id = task.get("id", "N/A")
            title = task.get("title", "N/A")[:50]
            task_status = task.get("status", "N/A")
            _print_terminal("  [{:12}] {}: {}", task_status, task_id, title)
    else:
        _print_error(response)


def list_goals(limit: int = 20):
    response = _request("GET", "/goals", timeout=15)
    if response.status_code != 200:
        _print_error(response)
        return
    goals = _api_data(response)
    if not isinstance(goals, list):
        goals = []
    print(f"Goals ({min(limit, len(goals))}/{len(goals)}):")
    for goal in goals[:limit]:
        print(
            "  [{:10}] {} (team={}) {}".format(
                _terminal(goal.get("status", "N/A")),
                _terminal(goal.get("id", "N/A")),
                _terminal(goal.get("team_id") or "-"),
                _terminal(str(goal.get("goal", ""))[:90]),
            )
        )


def show_goal_detail(goal_id: str):
    response = _request("GET", f"/goals/{goal_id}/detail", timeout=20)
    if response.status_code != 200:
        _print_error(response)
        return
    data = _api_data(response)
    goal = data.get("goal", {})
    trace = data.get("trace", {})
    artifacts = data.get("artifacts", {})
    summary = artifacts.get("result_summary", {})
    _print_terminal("Goal: {}", goal.get("id", goal_id))
    _print_terminal("  Status: {}", goal.get("status", "N/A"))
    _print_terminal("  Team: {}", goal.get("team_id") or "-")
    _print_terminal("  Trace: {}", trace.get("trace_id") or "-")
    print(f"  Tasks: total={summary.get('task_count', 0)} completed={summary.get('completed_tasks', 0)} failed={summary.get('failed_tasks', 0)}")
    headline = artifacts.get("headline_artifact") or {}
    if headline.get("preview"):
        _print_terminal("  Headline artifact: {}", str(headline.get("preview"))[:120])


def list_modes():
    response = _request("GET", "/goals/modes", timeout=10)
    if response.status_code != 200:
        _print_error(response)
        return
    modes = _api_data(response)
    if not isinstance(modes, list):
        modes = []
    print(f"Goal modes ({len(modes)}):")
    for mode in modes:
        _print_terminal("  - {}: {}", mode.get("id"), mode.get("title"))


def list_artifacts(limit: int = 20):
    response = _request("GET", "/artifacts", timeout=10)
    if response.status_code != 200:
        _print_error(response)
        return
    artifacts = _api_data(response)
    if not isinstance(artifacts, list):
        artifacts = []
    print(f"Artifacts ({min(limit, len(artifacts))}/{len(artifacts)}):")
    for artifact in artifacts[:limit]:
        _print_terminal(
            "  - {} [{}] {}",
            artifact.get("id", "N/A"),
            artifact.get("status", "N/A"),
            artifact.get("latest_filename") or artifact.get("latest_media_type") or "-",
        )


def analyze_task_followups(task_id: str, output: str | None = None):
    payload = {}
    if output:
        payload["output"] = output
    response = _request("POST", f"/tasks/auto-planner/analyze/{task_id}", body=payload, timeout=45)
    if response.status_code != 200:
        _print_error(response)
        return
    data = _api_data(response)
    followups = data.get("followups_created") or []
    _print_terminal("Follow-up analysis completed for task {}", task_id)
    print(f"  Follow-ups created: {len(followups)}")
    for followup in followups:
        _print_terminal("  - {}: {}", followup.get("id", "N/A"), str(followup.get("title", ""))[:80])


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


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="CLI for Ananta Goals, Tasks, Artifacts and Diagnostics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First run:
    python -m agent.cli_goals --first-run
    python -m agent.cli_goals --status
    python -m agent.cli_goals plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"

  Golden path (PRD-021): Short human-friendly commands:
    python -m agent.cli_goals ask "What should I do next?"
    python -m agent.cli_goals plan "Prepare a release checklist"
    python -m agent.cli_goals analyze "Find the riskiest frontend areas"
    python -m agent.cli_goals review "Review the auth changes"
    python -m agent.cli_goals diagnose "Docker frontend cannot reach hub"
    python -m agent.cli_goals patch "Fix failing login validation"
    python -m agent.cli_goals new-project "Build a small release-check tool for maintainers"
    python -m agent.cli_goals evolve-project "Add a guided project-start mode to the dashboard"
    python -m agent.cli_goals repair-admin "Service restart loop after update"

  Profile/Governance (GOV-051/PRF-080):
    python -m agent.cli_goals --config-show
    python -m agent.cli_goals --set-runtime-profile demo --set-governance-mode safe

  Submit guided mode:
    python -m agent.cli_goals --goal "Container restart-loop" --mode docker_compose_repair --mode-data '{"service":"hub"}'

  List tasks:
    python -m agent.cli_goals --tasks

  List goals:
    python -m agent.cli_goals --goals

  Goal detail:
    python -m agent.cli_goals --goal-detail <goal_id>

  List guided modes:
    python -m agent.cli_goals --modes

  Analyze follow-ups for a task:
    python -m agent.cli_goals --analyze-task <task_id>

  Check status:
    python -m agent.cli_goals --status
""",
    )

    parser.add_argument(
        "goal",
        nargs="?",
        help="Goal description to submit, or shortcut: ask/plan/analyze/review/diagnose/patch/new-project/evolve-project/repair-admin",
    )
    parser.add_argument("extra", nargs="*", help="Additional words for shortcut goals")
    parser.add_argument("--goal", "-g", dest="goal_flag", help="Goal description (alternative)")
    parser.add_argument("--context", "-c", help="Additional context for the goal")
    parser.add_argument("--team", "-t", help="Team ID to assign tasks to")
    parser.add_argument("--mode", help="Guided goal mode ID (e.g. code_fix, docker_compose_repair)")
    parser.add_argument("--mode-data", help='JSON object for mode fields, e.g. \'{"service":"hub"}\'')
    parser.add_argument("--no-create", action="store_true", help="Don't create tasks, just analyze")
    parser.add_argument("--status", "-s", action="store_true", help="Show Goal readiness + Auto-Planner status")
    parser.add_argument("--first-run", action="store_true", help="Show the official first CLI path, success signals and failure help")
    parser.add_argument("--goals", action="store_true", help="List goals")
    parser.add_argument("--goal-detail", help="Show detail for a goal ID")
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

    if args.status:
        show_status()
    elif args.goals:
        list_goals(limit=args.limit)
    elif args.goal_detail:
        show_goal_detail(args.goal_detail)
    elif args.modes:
        list_modes()
    elif args.tasks:
        list_tasks(status=args.task_status, limit=args.limit)
    elif args.artifacts:
        list_artifacts(limit=args.limit)
    elif args.analyze_task:
        analyze_task_followups(args.analyze_task, output=args.output)
    elif args.goal in SHORTCUT_GOALS:
        shortcut_text = " ".join(args.extra).strip()
        if not shortcut_text:
            print(f"Error: '{args.goal}' needs a short description")
            sys.exit(2)
        submit_shortcut(args.goal, shortcut_text, team_id=args.team, create_tasks=not args.no_create)
    elif args.goal or args.goal_flag:
        goal_text = args.goal or args.goal_flag
        if args.extra:
            goal_text = " ".join([goal_text, *args.extra])
        create_tasks = not args.no_create
        submit_goal(
            goal=goal_text,
            context=args.context,
            team_id=args.team,
            create_tasks=create_tasks,
            mode=args.mode,
            mode_data=_parse_mode_data(args.mode_data),
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
