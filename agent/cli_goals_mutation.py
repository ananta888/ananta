"""Goal submission and mutating CLI commands (SPLIT-013).

Hub I/O is injected through ``deps`` (:class:`CliGoalsDependencies`).
"""

from agent.cli_goals_support import (
    DEFAULT_DEPENDENCIES,
    SHORTCUT_GOALS,
    CliGoalsDependencies,
    parse_rag_sources,
    planning_mode_to_use_template,
    print_error,
    print_terminal,
    resolve_output_dir,
)


def submit_goal(
    goal: str,
    context: str | None = None,
    team_id: str | None = None,
    create_tasks: bool = True,
    mode: str | None = None,
    mode_data: dict | None = None,
    output_dir: str | None = None,
    planning_mode: str | None = None,
    rag_sources: str | None = None,
    *,
    deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES,
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
    if output_dir:
        container_path, host_path = resolve_output_dir(output_dir)
        payload.setdefault("execution_preferences", {})["output_dir"] = container_path
        if host_path:
            print_terminal("Output directory (host): {}", host_path)
        output_dir = container_path
    if rag_sources:
        parsed = parse_rag_sources(rag_sources)
        if parsed:
            payload.setdefault("execution_preferences", {})["rag_sources"] = parsed
    use_template = planning_mode_to_use_template(planning_mode)
    if use_template is not None:
        payload["use_template"] = use_template

    response = deps.request("POST", "/goals", body=payload, timeout=60)
    if response.status_code in {201, 202}:
        data = deps.api_data(response)
        goal_payload = data.get("goal", {})
        created_task_ids = data.get("created_task_ids", [])
        accepted_async = response.status_code == 202
        print_terminal("Goal submitted: {}", goal_payload.get("goal", goal))
        print_terminal("Goal ID: {}", goal_payload.get("id", "N/A"))
        print_terminal("Status: {}", goal_payload.get("status", "N/A"))
        if accepted_async:
            print_terminal("Dispatch: accepted (async planning)")
        print(f"Tasks created: {len(created_task_ids)}")
        for task_id in created_task_ids:
            print_terminal("  - {}", task_id)
        reference_profile = dict(goal_payload.get("reference_profile") or {})
        if reference_profile:
            print_terminal("Reference profile: {}", reference_profile.get("profile_id") or "-")
            print_terminal("Reference fit: {}", reference_profile.get("fit_level") or "n/a")
            if reference_profile.get("reason_summary"):
                print_terminal("Reference reason: {}", reference_profile.get("reason_summary"))
        goal_id = goal_payload.get("id")
        if goal_id:
            print_terminal("Next step: ananta goal --goal-detail {}", goal_id)
            if accepted_async:
                print_terminal("Next step: ananta goal --goal-tasks {}", goal_id)
        print("Success signal: Goal ID, status and task count are visible.")
        return created_task_ids
    print_error(response)
    return []


def submit_shortcut(
    kind: str,
    text: str,
    *,
    team_id: str | None = None,
    create_tasks: bool = True,
    output_dir: str | None = None,
    planning_mode: str | None = None,
    rag_sources: str | None = None,
    deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES,
):
    shortcut = SHORTCUT_GOALS.get(kind)
    if not shortcut:
        print_terminal("Error: Unknown shortcut '{}'. Available: {}", kind, ", ".join(sorted(SHORTCUT_GOALS)))
        return []
    shortcut_text = text.strip()
    goal_kwargs = {
        "goal": f"{shortcut['prefix']} {shortcut_text}",
        "context": shortcut["context"],
        "team_id": team_id,
        "create_tasks": create_tasks,
        "mode": shortcut.get("mode"),
        "mode_data": _shortcut_mode_data(kind, shortcut_text),
    }
    if output_dir is not None:
        goal_kwargs["output_dir"] = output_dir
    if planning_mode is not None:
        goal_kwargs["planning_mode"] = planning_mode
    if rag_sources is not None:
        goal_kwargs["rag_sources"] = rag_sources
    return submit_goal(**goal_kwargs, deps=deps)


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
        data["no_task_dependencies"] = True
    return data


def purge_goal(
    goal_id: str, *, include_prompt_traces: bool = True, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES
) -> int:
    goal_id_norm = str(goal_id or "").strip()
    if not goal_id_norm:
        print("Error: --goal-purge requires a goal ID")
        return 2
    response = deps.request(
        "DELETE",
        f"/goals/{goal_id_norm}/purge",
        params={"include_prompt_traces": "1" if include_prompt_traces else "0"},
        timeout=60,
    )
    if response.status_code != 200:
        print_error(response)
        return 1
    data = deps.api_data(response) or {}
    deleted = data.get("deleted") or {}
    print_terminal("Goal purged: {}", data.get("goal_id") or goal_id_norm)
    print(f"  Deleted total: {int(data.get('deleted_total') or 0)}")
    print(f"  Prompt traces deleted: {int(data.get('prompt_traces_deleted') or 0)}")
    if isinstance(deleted, dict):
        for key in sorted(deleted.keys()):
            print(f"  - {key}: {int(deleted.get(key) or 0)}")
    return 0


def recover_stale(*, dry_run: bool = True, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES) -> int:
    """PRI-013: Cancel stale planning goals with expired lease."""
    # Re-uses the preflight logic exposed via the health endpoint + a dedicated recover route.
    # For now: call the health endpoint to report, then DELETE stale goals directly.
    response = deps.request("GET", "/goals/planning/health", timeout=15)
    if response.status_code == 403:
        print("Error: --recover-stale requires admin credentials")
        return 2
    if response.status_code != 200:
        print_error(response)
        return 1
    data = deps.api_data(response) or {}
    stale = int((data.get("goals") or {}).get("stale_expired_lease") or 0)
    if stale == 0:
        print("No stale planning goals found.")
        return 0
    if dry_run:
        print(f"[DRY RUN] Would cancel {stale} stale planning goal(s). Use --recover-stale --yes to execute.")
        return 0
    # POST to trigger server-side recovery.
    rec_response = deps.request("POST", "/goals/planning/recover-stale", timeout=30)
    if rec_response.status_code == 404:
        # Endpoint not yet available — inform operator.
        print(f"Server-side recover endpoint not available. Use --goal-purge or direct DB cleanup for {stale} stale goal(s).")
        return 1
    if rec_response.status_code != 200:
        print_error(rec_response)
        return 1
    rec_data = deps.api_data(rec_response) or {}
    print(f"Cancelled {rec_data.get('cancelled', stale)} stale planning goal(s).")
    return 0


def cancel_tree(goal_id: str, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES) -> int:
    """PRI-013: Cancel all tasks for a goal and mark it failed via purge or lifecycle transition."""
    goal_id_norm = str(goal_id or "").strip()
    if not goal_id_norm:
        print("Error: --cancel-tree requires a goal ID")
        return 2
    # Use the lifecycle cancel endpoint if available, else fall back to purge.
    cancel_response = deps.request("POST", f"/goals/{goal_id_norm}/cancel", timeout=30)
    if cancel_response.status_code == 404:
        print(f"No dedicated cancel endpoint — using purge for goal {goal_id_norm}")
        return purge_goal(goal_id_norm, deps=deps)
    if cancel_response.status_code != 200:
        print_error(cancel_response)
        return 1
    data = deps.api_data(cancel_response) or {}
    print_terminal("Goal cancelled: {}", goal_id_norm)
    print(f"  Tasks cancelled: {data.get('tasks_cancelled', '?')}")
    worker_failures = data.get("worker_cancel_failures") or []
    if worker_failures:
        print(f"  WARNING: {len(worker_failures)} worker(s) did not ack cancel:")
        for f in worker_failures[:5]:
            print(f"    task={f.get('task_id')} url={f.get('worker_url')} error={f.get('error')}")
    return 0


def kill_requests(goal_id: str, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES) -> int:
    """Abort all in-flight LM Studio requests for a goal (without cancelling the goal)."""
    goal_id_norm = str(goal_id or "").strip()
    if not goal_id_norm:
        print("Error: --kill-requests requires a goal ID")
        return 2
    response = deps.request("POST", f"/goals/{goal_id_norm}/kill-requests", timeout=15)
    if response.status_code != 200:
        print_error(response)
        return 1
    data = deps.api_data(response) or {}
    killed = data.get("sessions_killed", 0)
    print_terminal("Killed {} in-flight LM Studio request(s) for goal {}", killed, goal_id_norm)
    remaining = data.get("active_counts") or {}
    if remaining:
        print(f"  Active requests remaining: {remaining}")
    return 0


def kill_all_requests(*, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES) -> int:
    """Abort all in-flight LM Studio requests across all goals."""
    response = deps.request("POST", "/goals/kill-all-requests", timeout=15)
    if response.status_code != 200:
        print_error(response)
        return 1
    data = deps.api_data(response) or {}
    killed = data.get("sessions_killed", 0)
    print_terminal("Killed {} in-flight LM Studio request(s) across all goals", killed)
    return 0


def analyze_task_followups(
    task_id: str, output: str | None = None, *, deps: CliGoalsDependencies = DEFAULT_DEPENDENCIES
):
    payload = {}
    if output:
        payload["output"] = output
    response = deps.request("POST", f"/tasks/auto-planner/analyze/{task_id}", body=payload, timeout=45)
    if response.status_code != 200:
        print_error(response)
        return
    data = deps.api_data(response)
    followups = data.get("followups_created") or []
    print_terminal("Follow-up analysis completed for task {}", task_id)
    print(f"  Follow-ups created: {len(followups)}")
    for followup in followups:
        print_terminal("  - {}: {}", followup.get("id", "N/A"), str(followup.get("title", ""))[:80])
