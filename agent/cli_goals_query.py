"""Read-only goal/task/artifact CLI commands (SPLIT-013).

Hub I/O is injected through ``deps`` (:class:`CliGoalsDependencies`).
"""

from agent.cli_goals_support import (
    DEFAULT_DEPENDENCIES,
    CliGoalsDependencies,
    print_error,
    print_terminal,
    read_json,
    terminal_text,
)


def show_first_run(*, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    base_url = deps.base_url()
    print("Ananta CLI First Run")
    print("=====================")
    print_terminal("Hub URL: {}", base_url)
    print("\n1. Optional environment:")
    print_terminal("   export ANANTA_BASE_URL={}", base_url)
    print("   export ANANTA_USER=admin")
    print("   export ANANTA_PASSWORD=<password>")
    print("\n2. Readiness check:")
    print("   ananta status")
    print("\n3. Official first goal:")
    print('   ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"')
    print("\nSuccess signal:")
    print("   - Goal ID is printed")
    print("   - Status is printed")
    print("   - Tasks created is greater than 0 for a planned goal")
    print("\nAfter success:")
    print("   ananta goal --tasks --task-status todo")
    print("   ananta goal --goal-detail <goal_id>")
    print("\nIf it fails:")
    print("   - login error: check ANANTA_USER/ANANTA_PASSWORD")
    print("   - connection error: start the hub or set ANANTA_BASE_URL")
    print("   - governance/policy block: narrow the goal or inspect the governance mode")


def show_status(*, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    readiness_res = deps.request("GET", "/goals/readiness", timeout=10)
    if readiness_res.status_code == 200:
        readiness = deps.api_data(readiness_res)
        print("Goal Readiness:")
        print(f"  Happy path ready: {readiness.get('happy_path_ready', False)}")
        print(f"  Planning available: {readiness.get('planning_available', False)}")
        print(f"  Worker available: {readiness.get('worker_available', False)}")
        print_terminal("  Active team: {}", readiness.get("active_team_id") or "-")
    else:
        print_error(readiness_res)

    planner_res = deps.request("GET", "/tasks/auto-planner/status", timeout=10)
    if planner_res.status_code == 200:
        data = deps.api_data(planner_res)
        stats = data.get("stats", {})
        print("\nAuto-Planner Status:")
        print(f"  Enabled: {data.get('enabled', False)}")
        print(f"  Goals processed: {stats.get('goals_processed', 0)}")
        print(f"  Tasks created: {stats.get('tasks_created', 0)}")
        print(f"  Follow-ups created: {stats.get('followups_created', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")
    else:
        print_error(planner_res)


def list_tasks(status: str = None, limit: int = 20, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    params = {"limit": limit}
    if status:
        params["status"] = status

    response = deps.request("GET", "/tasks", params=params, timeout=10)

    if response.status_code == 200:
        tasks = read_json(response)
        if isinstance(tasks, dict):
            tasks = tasks.get("data", [])

        print(f"Tasks ({len(tasks)}):")
        for task in tasks[:limit]:
            task = task if isinstance(task, dict) else {}
            task_id = task.get("id", "N/A")
            raw_title = task.get("title")
            title = str(raw_title)[:50] if raw_title is not None else "N/A"
            task_status = task.get("status", "N/A")
            print_terminal("  [{:12}] {}: {}", task_status, task_id, title)
    else:
        print_error(response)


def list_goals(limit: int = 20, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    response = deps.request("GET", "/goals", timeout=15)
    if response.status_code != 200:
        print_error(response)
        return
    goals = deps.api_data(response)
    if not isinstance(goals, list):
        goals = []
    print(f"Goals ({min(limit, len(goals))}/{len(goals)}):")
    print(f"  {'Status':12s} {'Tasks':5s}  {'Goal ID':36s}  {'Description'}")
    print("  " + "-"*12 + " " + "-"*5 + "  " + "-"*36 + "  " + "-"*50)
    for goal in goals[:limit]:
        task_count = int(goal.get("task_count") or 0)
        print(
            "  [{:10}] {:>3}   {}  {}".format(
                terminal_text(goal.get("status", "N/A")),
                task_count,
                terminal_text(goal.get("id", "N/A")),
                terminal_text(str(goal.get("goal", ""))[:90]),
            )
        )


def list_goal_tasks(goal_id: str, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    response = deps.request("GET", f"/goals/{goal_id}/detail", timeout=30)
    if response.status_code != 200:
        print_error(response)
        return
    data = deps.api_data(response) or {}
    tasks = list(data.get("tasks") or [])
    goal = data.get("goal") or {}
    print(f"=== Tasks for Goal {goal_id} ===")
    print(f"  Status: {goal.get('status', 'N/A')}")
    print(f"  Tasks:  {len(tasks)}")
    print()
    if not tasks:
        print("  (no tasks)")
        return
    print("  {:44s} {:12s} {:8s}  {}".format("ID", "Status", "Priority", "Title"))
    print("  " + "-"*44 + " " + "-"*12 + " " + "-"*8 + "  " + "-"*60)
    for task in tasks:
        print(
            "  {:44s} [{:10}] {:7s}  {}".format(
                str(task.get("id", ""))[:44],
                terminal_text(task.get("status", "N/A")),
                str(task.get("priority", "") or "-"),
                str(task.get("title", ""))[:60],
            )
        )


def show_goal_detail(goal_id: str, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    response = deps.request("GET", f"/goals/{goal_id}/detail", timeout=20)
    if response.status_code != 200:
        print_error(response)
        return
    data = deps.api_data(response)
    goal = data.get("goal", {})
    trace = data.get("trace", {})
    artifacts = data.get("artifacts", {})
    summary = artifacts.get("result_summary", {})
    print_terminal("Goal: {}", goal.get("id", goal_id))
    print_terminal("  Status: {}", goal.get("status", "N/A"))
    print_terminal("  Team: {}", goal.get("team_id") or "-")
    print_terminal("  Trace: {}", trace.get("trace_id") or "-")
    print(f"  Tasks: total={summary.get('task_count', 0)} completed={summary.get('completed_tasks', 0)} failed={summary.get('failed_tasks', 0)}")
    headline = artifacts.get("headline_artifact") or {}
    if headline.get("preview"):
        print_terminal("  Headline artifact: {}", str(headline.get("preview"))[:120])


def planning_stuck(*, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES) -> int:
    """PRI-013: List goals with expired planning lease (stuck in planning_running/queued)."""
    response = deps.request("GET", "/goals/planning/health", timeout=15)
    if response.status_code == 403:
        print("Error: --planning-stuck requires admin credentials")
        return 2
    if response.status_code != 200:
        print_error(response)
        return 1
    data = deps.api_data(response) or {}
    goals = data.get("goals") or {}
    slots = data.get("planning_slots") or {}
    cb = data.get("circuit_breaker") or {}
    print("=== Planning Health ===")
    print(f"  Slots: capacity={slots.get('capacity')} in_use={slots.get('in_use')} available={slots.get('available')}")
    print(f"  Goals queued: {goals.get('queued')}  running: {goals.get('running')}  stale_expired_lease: {goals.get('stale_expired_lease')}")
    print(f"  Circuit breaker [{cb.get('provider')}]: state={cb.get('state')} failures={cb.get('failures')}/{cb.get('threshold')}")
    stale = int(goals.get("stale_expired_lease") or 0)
    if stale:
        print(f"\n  WARNING: {stale} goal(s) have an expired planning lease — run --recover-stale to clean up.")
    return 0


def list_modes(*, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    response = deps.request("GET", "/goals/modes", timeout=10)
    if response.status_code != 200:
        print_error(response)
        return
    modes = deps.api_data(response)
    if not isinstance(modes, list):
        modes = []
    print(f"Goal modes ({len(modes)}):")
    for mode in modes:
        print_terminal("  - {}: {}", mode.get("id"), mode.get("title"))


def list_artifacts(limit: int = 20, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES):
    response = deps.request("GET", "/artifacts", timeout=10)
    if response.status_code != 200:
        print_error(response)
        return
    artifacts = deps.api_data(response)
    if not isinstance(artifacts, list):
        artifacts = []
    print(f"Artifacts ({min(limit, len(artifacts))}/{len(artifacts)}):")
    for artifact in artifacts[:limit]:
        print_terminal(
            "  - {} [{}] {}",
            artifact.get("id", "N/A"),
            artifact.get("status", "N/A"),
            artifact.get("latest_filename") or artifact.get("latest_media_type") or "-",
        )
