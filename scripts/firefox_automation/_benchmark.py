#!/usr/bin/env python3
"""Live-click benchmark phase: goal follow-up, terminal and model benchmark.

The phase is a sequence of named steps from ``_benchmark_phase_steps``; this
module only orders them and derives the verdict (SRP: step mechanics live in
the step module, the phase owns the flow and the pass/fail decision).
"""

from __future__ import annotations

import time
from typing import List

from firefox_automation._benchmark_helpers import (
    _collect_goal_tasks_snapshot,
    _extract_file_path_evidence,
    _open_operations_console_artifact_flow,
    browser_api_json,
)
from firefox_automation._benchmark_phase_steps import (
    GoalTaskObservation,
    TerminalProbe,
    any_active_terminal,
    bind_online_workers_to_team,
    choose_terminal_agent,
    load_agent_entries,
    load_benchmark_goals,
    load_goal_detail_after,
    load_goal_detail_before,
    model_usage_ok,
    new_recovery_info,
    observe_goal_tasks,
    observed_terminal_models,
    online_worker_agents,
    pick_benchmark_goal,
    preferred_worker_urls,
    record_model_benchmark,
    recovery_fallback_signal,
    resolve_provider_and_model,
    retarget_tasks_to_expected_team,
    run_autopilot_tick_loop,
    run_failure_recovery,
    select_terminal_task,
    summarize_goal_artifacts,
    summarize_orchestration_read_model,
    task_detail_terminal_ok,
    task_ids,
    worker_availability,
)
from firefox_automation._browser_utils import current_route_and_title, settle, step_nav
from firefox_automation._reporting import gate_visible_errors, record_step


def phase_benchmark(
    session_id: str,
    report: dict,
    hard_fail: bool,
    step_delay_seconds: float,
    benchmark_ticks: int,
    benchmark_task_kind: str,
    require_followup: bool,
    require_artifact_summary: bool,
    require_multi_file_output: bool,
    min_distinct_files: int,
    min_distinct_dirs: int,
):
    t0 = time.time()
    step_nav(session_id, "/auto-planner")
    goals = load_benchmark_goals(session_id, report, t0)
    picked_goal = pick_benchmark_goal(goals, report)
    goal_id = str((picked_goal or {}).get("id") or "")
    if not goal_id:
        record_step(report, "benchmark", "collect_goals", t0, False, {"goal_count": len(goals)})
        raise RuntimeError("No goal id available for benchmark phase")

    detail_before = load_goal_detail_before(session_id, report, t0, goal_id)
    tasks_before = detail_before.get("tasks") if isinstance(detail_before, dict) else []
    tasks_before = tasks_before if isinstance(tasks_before, list) else []
    goal_trace_id = str((detail_before.get("trace") or {}).get("trace_id") or "")
    tasks_before_full = _collect_goal_tasks_snapshot(
        session_id,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        timeout_seconds=75,
    )
    tasks_before_full_ids = task_ids(tasks_before_full)
    team_id = str((detail_before.get("goal") or {}).get("team_id") or "")
    team_id, task_team_patch_info = retarget_tasks_to_expected_team(session_id, report, team_id, tasks_before_full)
    if team_id:
        report.setdefault("cleanup_targets", {}).setdefault("team_ids", []).append(team_id)

    binding = bind_online_workers_to_team(session_id, team_id)
    worker_bind_info = binding.info
    agent_entries = binding.agent_entries or load_agent_entries(session_id)
    online_agents = online_worker_agents(agent_entries)
    probe = TerminalProbe(
        agent=choose_terminal_agent(preferred_worker_urls(binding.team_obj, worker_bind_info), online_agents),
        online_agents=online_agents,
    )
    probe.refine_from_tasks(tasks_before_full)

    tick_results: List[dict] = []
    autopilot_stop_before_res = browser_api_json(
        session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30
    )
    autopilot_start_payload = {
        "team_id": team_id or None,
        "max_concurrency": 2,
        "security_level": "balanced",
    }
    autopilot_start_res = browser_api_json(
        session_id, "POST", "/tasks/autopilot/start", body=autopilot_start_payload, timeout_seconds=45
    )
    tick_start = time.time()
    tick_body = {"team_id": team_id} if team_id else {}
    initial_tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=25)
    tick_results.append(initial_tick_res)
    run_autopilot_tick_loop(
        session_id,
        benchmark_ticks=benchmark_ticks,
        tick_body=tick_body,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        probe=probe,
        tick_results=tick_results,
    )
    autopilot_total_ms = int((time.time() - tick_start) * 1000)

    observation = _observe_goal_after_ticks(session_id, goal_id, goal_trace_id, tasks_before_full_ids, probe)
    followup_created = observation.followup_observed
    recovery_info = new_recovery_info()

    # Recovery path: if initial execution terminalized with failures only, force follow-up creation
    # and re-run a few ticks so benchmark can validate an actual iterative flow.
    first_status = observation.after_status
    if first_status["completed"] == 0 and first_status["failed"] > 0 and not followup_created:
        run_failure_recovery(
            session_id,
            tasks_after=observation.tasks_after,
            team_id=team_id,
            goal_id=goal_id,
            benchmark_ticks=benchmark_ticks,
            tick_results=tick_results,
            recovery_info=recovery_info,
        )
        observation = _observe_goal(session_id, goal_id, goal_trace_id, tasks_before_full_ids)
        followup_created = observation.followup_observed or recovery_fallback_signal(recovery_info)
    after_status = observation.after_status

    provider, model = resolve_provider_and_model(session_id)
    artifacts = summarize_goal_artifacts(observation.detail_after)
    artifact_flow_summary, reconciliation_summary = summarize_orchestration_read_model(
        session_id, observation.tasks_after_full
    )
    operations_console = _open_operations_console_artifact_flow(session_id)
    file_evidence = _extract_file_path_evidence(observation.tasks_after_full)
    multi_file_output_ok = int(file_evidence.get("distinct_file_count") or 0) >= int(
        max(1, min_distinct_files)
    ) and int(file_evidence.get("distinct_dir_count") or 0) >= int(max(1, min_distinct_dirs))
    terminal_buffer = str(probe.view.get("buffer_excerpt") or "").lower()
    terminal_cli_visible = bool(probe.view.get("cli_command_visible")) or "opencode run" in terminal_buffer
    terminal_workdir_error = "failed to change directory" in terminal_buffer
    terminal_signal_ok = (
        terminal_cli_visible or probe.active_session_found or bool(probe.task_detail.get("embedded_visible"))
    )
    terminal_models = observed_terminal_models(observation.tasks_after_full)
    models_used_ok = model_usage_ok(artifacts.result_summary, terminal_models, model)

    benchmark_success = _benchmark_success(
        base_success=after_status["completed"] > 0 or probe.active_session_found,
        required_checks=(
            (require_followup, observation.followup_observed),
            (require_artifact_summary, artifacts.ok),
            (require_multi_file_output, multi_file_output_ok),
        ),
        final_checks=(
            terminal_signal_ok,
            not terminal_workdir_error,
            models_used_ok,
            task_detail_terminal_ok(probe.task_detail),
        ),
    )
    benchmark_payload = {
        "provider": provider,
        "model": model,
        "task_kind": benchmark_task_kind,
        "success": benchmark_success,
        "quality_gate_passed": benchmark_success,
        "latency_ms": autopilot_total_ms,
        "tokens_total": 0,
    }
    model_key = f"{provider}:{model}"
    bench_record_res, bench_focus = record_model_benchmark(
        session_id, benchmark_payload, benchmark_task_kind, model_key
    )

    autopilot_stop_res = browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)
    if probe.agent and probe.forward_param:
        probe.open_worker_panel(session_id)
    if isinstance(probe.selected_task, dict):
        probe.inspect_selected_task(session_id)

    workers_available_max, workers_online_max = worker_availability(tick_results)
    no_worker_blocker = workers_available_max == 0 and workers_online_max == 0
    progress_signal = (
        after_status["completed"] > 0
        or followup_created
        or observation.terminalized
        or probe.active_session_found
        or terminal_signal_ok
        or no_worker_blocker
    )
    ok = _api_ok(autopilot_start_res) and _api_ok(bench_record_res) and after_status["total"] > 0 and progress_signal
    if require_followup or require_artifact_summary or require_multi_file_output:
        ok = ok and benchmark_success
    record_step(
        report,
        "benchmark",
        "goal_followup_and_model_benchmark",
        t0,
        ok,
        {
            "goal_id": goal_id,
            "goal_summary": str((picked_goal or {}).get("summary") or ""),
            "worker_bind_info": worker_bind_info,
            "task_team_patch_info": task_team_patch_info,
            "worker_terminal": probe.view,
            "task_detail_terminal": probe.task_detail,
            "active_terminal_session_found": probe.active_session_found,
            "observed_terminal_models": terminal_models[:10],
            "terminal_cli_visible": terminal_cli_visible,
            "terminal_signal_ok": terminal_signal_ok,
            "terminal_workdir_error": terminal_workdir_error,
            "model_usage_ok": models_used_ok,
            "tasks_before": len(tasks_before),
            "tasks_after": len(observation.tasks_after),
            "tasks_before_full": len(tasks_before_full),
            "tasks_after_full": len(observation.tasks_after_full),
            "new_task_ids": observation.new_task_ids,
            "followup_task_ids": observation.followup_task_ids,
            "followup_created": followup_created,
            "followup_observed": observation.followup_observed,
            "terminalized": observation.terminalized,
            "fibonacci_mentions_in_tasks": observation.fib_mentions,
            "artifacts_summary_present": artifacts.ok,
            "result_summary": artifacts.result_summary if isinstance(artifacts.result_summary, dict) else {},
            "headline_artifact_preview": str((artifacts.headline_artifact or {}).get("preview") or "")[:280],
            "artifact_flow": artifact_flow_summary,
            "execution_reconciliation": reconciliation_summary,
            "operations_console": operations_console,
            "file_output_evidence": file_evidence,
            "multi_file_output_ok": multi_file_output_ok,
            "require_followup": require_followup,
            "require_artifact_summary": require_artifact_summary,
            "require_multi_file_output": require_multi_file_output,
            "min_distinct_files": int(max(1, min_distinct_files)),
            "min_distinct_dirs": int(max(1, min_distinct_dirs)),
            "autopilot_ticks_requested": int(benchmark_ticks),
            "autopilot_total_ms": autopilot_total_ms,
            "autopilot_start_payload": autopilot_start_payload,
            "autopilot_stop_before_status": int(autopilot_stop_before_res.get("status") or 0),
            "autopilot_start_status": int(autopilot_start_res.get("status") or 0),
            "autopilot_stop_status": int(autopilot_stop_res.get("status") or 0),
            "workers_available_max": workers_available_max,
            "workers_online_max": workers_online_max,
            "no_worker_blocker": no_worker_blocker,
            "autopilot_tick_results": tick_results,
            "recovery_info": recovery_info,
            "benchmark_payload": benchmark_payload,
            "benchmark_record_status": int(bench_record_res.get("status") or 0),
            "benchmark_model_key": model_key,
            "benchmark_focus": bench_focus,
            "task_status_after": after_status,
            **current_route_and_title(session_id),
        },
    )
    settle(step_delay_seconds)
    gate_visible_errors(session_id, report, "benchmark", hard_fail)
    if not ok:
        raise RuntimeError("Benchmark phase failed")


def _api_ok(response: dict) -> bool:
    return bool(response.get("ok")) and int(response.get("status") or 0) < 400


def _benchmark_success(
    *,
    base_success: bool,
    required_checks: tuple[tuple[bool, bool], ...],
    final_checks: tuple[bool, ...],
) -> bool:
    """Combine the base signal, the opted-in requirements and the terminal/model checks."""

    success = base_success
    for required, passed in required_checks:
        if required:
            success = success and passed
    return success and all(final_checks)


def _observe_goal(
    session_id: str,
    goal_id: str,
    goal_trace_id: str,
    tasks_before_full_ids: set[str],
) -> GoalTaskObservation:
    detail_after, tasks_after = load_goal_detail_after(session_id, goal_id)
    tasks_after_full = _collect_goal_tasks_snapshot(
        session_id,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        timeout_seconds=75,
    )
    return observe_goal_tasks(detail_after, tasks_after, tasks_after_full, tasks_before_full_ids)


def _observe_goal_after_ticks(
    session_id: str,
    goal_id: str,
    goal_trace_id: str,
    tasks_before_full_ids: set[str],
    probe: TerminalProbe,
) -> GoalTaskObservation:
    """Observe the goal after the tick loop and finish terminal selection."""

    observation = _observe_goal(session_id, goal_id, goal_trace_id, tasks_before_full_ids)
    if probe.agent and not probe.forward_param:
        probe.refine_from_tasks(observation.tasks_after_full)
    probe.selected_task = probe.selected_task or select_terminal_task(
        observation.tasks_after_full, probe.preferred_agent_url()
    )
    probe.active_session_found = probe.active_session_found or any_active_terminal(observation.tasks_after_full)
    return observation
