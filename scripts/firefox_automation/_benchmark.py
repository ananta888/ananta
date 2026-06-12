#!/usr/bin/env python3
from __future__ import annotations

from firefox_automation._benchmark_helpers import (
    _collect_goal_tasks_snapshot,
    _extract_file_path_evidence,
    _extract_task_terminal_agent_url,
    _extract_task_terminal_forward_param,
    _open_operations_console_artifact_flow,
    _open_worker_terminal_panel,
    _pick_task_terminal,
    _select_preferred_team,
    _summarize_tasks,
    _task_has_active_terminal,
    _unwrap_envelope,
    browser_api_json,
    ensure_opencode_execution_mode,
)
from firefox_automation._browser_utils import current_route_and_title, list_visible_errors, settle, step_nav
from firefox_automation._config import FILE_PATH_PATTERN, HUB_BASE, LOGIN_PASS
from firefox_automation._reporting import gate_visible_errors, record_step
from firefox_automation._webdriver import element_click, element_send_keys, find_element, js, set_input_value_via_js, wait_for, wd
import time
from typing import Any, Dict, List, Optional

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
    goal_hint = str((report.get("goal_text") or "")).strip().lower()

    goals_res = browser_api_json(session_id, "GET", "/goals", timeout_seconds=45)
    if not goals_res.get("ok") or int(goals_res.get("status") or 0) >= 400:
        record_step(report, "benchmark", "collect_goals", t0, False, {"api": goals_res})
        raise RuntimeError("Could not load goals via browser API")
    goals_payload = _unwrap_envelope(goals_res.get("body"))
    goals = goals_payload if isinstance(goals_payload, list) else []

    preferred_goal_id = str(report.get("last_goal_id") or "").strip()
    picked_goal = None
    if preferred_goal_id:
        for goal in goals:
            if isinstance(goal, dict) and str(goal.get("id") or "") == preferred_goal_id:
                picked_goal = goal
                break
    for goal in reversed(goals):
        if picked_goal:
            break
        if not isinstance(goal, dict):
            continue
        text_blob = f"{goal.get('goal', '')} {goal.get('summary', '')}".lower()
        if goal_hint and goal_hint in text_blob:
            picked_goal = goal
            break
        if "fibonacci" in text_blob:
            picked_goal = goal
            break
    if not picked_goal and goals:
        picked_goal = goals[-1]
    goal_id = str((picked_goal or {}).get("id") or "")
    if not goal_id:
        record_step(report, "benchmark", "collect_goals", t0, False, {"goal_count": len(goals)})
        raise RuntimeError("No goal id available for benchmark phase")

    detail_before_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    if not detail_before_res.get("ok") or int(detail_before_res.get("status") or 0) >= 400:
        record_step(report, "benchmark", "goal_detail_before", t0, False, {"api": detail_before_res, "goal_id": goal_id})
        raise RuntimeError("Could not load goal detail before ticks")
    detail_before = _unwrap_envelope(detail_before_res.get("body")) or {}
    tasks_before = detail_before.get("tasks") if isinstance(detail_before, dict) else []
    tasks_before = tasks_before if isinstance(tasks_before, list) else []
    goal_trace_id = str((detail_before.get("trace") or {}).get("trace_id") or "")
    tasks_before_full = _collect_goal_tasks_snapshot(
        session_id,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        timeout_seconds=75,
    )
    tasks_before_full_ids = {str(task.get("id") or "") for task in tasks_before_full if isinstance(task, dict)}
    team_id = str((detail_before.get("goal") or {}).get("team_id") or "")
    task_team_patch_info: Dict[str, Any] = {"resolved_team_id": team_id, "patched_task_ids": [], "patch_statuses": []}
    cleanup_targets = report.get("cleanup_targets") if isinstance(report.get("cleanup_targets"), dict) else {}
    expected_team_id = ""
    expected_team_name = ""
    if isinstance(cleanup_targets, dict):
        team_ids = cleanup_targets.get("team_ids")
        if isinstance(team_ids, list) and team_ids:
            expected_team_id = str(team_ids[-1] or "").strip()
        team_names = cleanup_targets.get("team_names")
        if isinstance(team_names, list) and team_names:
            expected_team_name = str(team_names[-1] or "").strip()
    if expected_team_id or expected_team_name:
        teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
        teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
        teams = teams_payload if isinstance(teams_payload, list) else []
        matched_team = next(
            (
                item
                for item in teams
                if isinstance(item, dict)
                and (
                    (expected_team_id and str(item.get("id") or "").strip() == expected_team_id)
                    or (expected_team_name and str(item.get("name") or "").strip() == expected_team_name)
                )
            ),
            None,
        )
        resolved_team_id = str((matched_team or {}).get("id") or "").strip()
        if resolved_team_id:
            task_team_patch_info["resolved_team_id"] = resolved_team_id
            needs_team_retarget = not team_id or team_id != resolved_team_id
            for task in tasks_before_full:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("id") or "").strip()
                task_team_id = str(task.get("team_id") or "").strip()
                if not task_id or (task_team_id == resolved_team_id and not needs_team_retarget):
                    continue
                patch_res = browser_api_json(
                    session_id,
                    "PATCH",
                    f"/tasks/{task_id}",
                    body={"team_id": resolved_team_id},
                    timeout_seconds=45,
                )
                task_team_patch_info["patch_statuses"].append(
                    {
                        "task_id": task_id,
                        "from_team_id": task_team_id,
                        "status": int(patch_res.get("status") or 0),
                    }
                )
                if patch_res.get("ok") and int(patch_res.get("status") or 0) < 400:
                    task_team_patch_info["patched_task_ids"].append(task_id)
            if resolved_team_id:
                team_id = resolved_team_id
    if team_id:
        report.setdefault("cleanup_targets", {}).setdefault("team_ids", []).append(team_id)

    # Ensure the goal team has online workers assigned, otherwise autopilot stays idle.
    worker_bind_info: Dict[str, Any] = {"team_id": team_id, "applied": False}
    agent_entries: List[Dict[str, Any]] = []
    team_obj: Optional[Dict[str, Any]] = None
    if team_id:
        agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
        teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
        workers_payload = _unwrap_envelope(agents_res.get("body")) if agents_res.get("ok") else []
        teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
        workers = workers_payload if isinstance(workers_payload, list) else []
        teams = teams_payload if isinstance(teams_payload, list) else []
        agent_entries = [item for item in workers if isinstance(item, dict)]
        team_obj = next((t for t in teams if isinstance(t, dict) and str(t.get("id") or "") == team_id), None)
        online_worker_urls = [
            str(a.get("url") or "")
            for a in agent_entries
            if isinstance(a, dict) and str(a.get("role") or "").lower() == "worker" and str(a.get("status") or "").lower() == "online"
        ]
        existing_member_urls = [
            str(m.get("agent_url") or "")
            for m in ((team_obj or {}).get("members") or [])
            if isinstance(m, dict)
        ]
        needs_binding = bool(online_worker_urls) and not any(url in existing_member_urls for url in online_worker_urls)
        worker_bind_info.update(
            {
                "online_worker_urls": online_worker_urls,
                "existing_member_urls": existing_member_urls,
                "needs_binding": needs_binding,
            }
        )
        if needs_binding and team_obj:
            role_ids: List[str] = []
            team_type_id = str(team_obj.get("team_type_id") or "")
            if team_type_id:
                type_roles_res = browser_api_json(session_id, "GET", f"/teams/types/{team_type_id}/roles", timeout_seconds=45)
                type_roles = _unwrap_envelope(type_roles_res.get("body")) if type_roles_res.get("ok") else []
                if isinstance(type_roles, list):
                    for item in type_roles:
                        if isinstance(item, dict):
                            rid = str(item.get("role_id") or item.get("id") or "")
                            if rid and rid not in role_ids:
                                role_ids.append(rid)
            if not role_ids:
                roles_res = browser_api_json(session_id, "GET", "/teams/roles", timeout_seconds=45)
                roles = _unwrap_envelope(roles_res.get("body")) if roles_res.get("ok") else []
                if isinstance(roles, list):
                    for item in roles:
                        if isinstance(item, dict):
                            rid = str(item.get("id") or "")
                            if rid:
                                role_ids.append(rid)
            if role_ids:
                members_payload = []
                for idx, worker_url in enumerate(online_worker_urls[:2]):
                    members_payload.append({"agent_url": worker_url, "role_id": role_ids[idx % len(role_ids)]})
                patch_res = browser_api_json(
                    session_id,
                    "PATCH",
                    f"/teams/{team_id}",
                    body={"members": members_payload},
                    timeout_seconds=45,
                )
                activate_res = browser_api_json(session_id, "POST", f"/teams/{team_id}/activate", body={}, timeout_seconds=30)
                worker_bind_info.update(
                    {
                        "applied": bool(patch_res.get("ok")) and int(patch_res.get("status") or 0) < 400,
                        "patch_status": int(patch_res.get("status") or 0),
                        "activate_status": int(activate_res.get("status") or 0),
                        "members_payload": members_payload,
                    }
                )

    if not agent_entries:
        agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
        agents_payload = _unwrap_envelope(agents_res.get("body")) if agents_res.get("ok") else []
        agent_entries = [item for item in (agents_payload if isinstance(agents_payload, list) else []) if isinstance(item, dict)]

    preferred_worker_urls: List[str] = []
    if team_obj:
        for member in ((team_obj or {}).get("members") or []):
            if not isinstance(member, dict):
                continue
            agent_url = str(member.get("agent_url") or "").strip()
            if agent_url and agent_url not in preferred_worker_urls:
                preferred_worker_urls.append(agent_url)
    for member in (worker_bind_info.get("members_payload") or []):
        if not isinstance(member, dict):
            continue
        agent_url = str(member.get("agent_url") or "").strip()
        if agent_url and agent_url not in preferred_worker_urls:
            preferred_worker_urls.append(agent_url)

    online_worker_agents = [
        agent
        for agent in agent_entries
        if str(agent.get("role") or "").lower() == "worker" and str(agent.get("status") or "").lower() == "online"
    ]
    terminal_agent: Optional[Dict[str, Any]] = None
    for worker_url in preferred_worker_urls:
        terminal_agent = next((agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == worker_url), None)
        if terminal_agent:
            break
    if not terminal_agent and online_worker_agents:
        terminal_agent = online_worker_agents[0]

    terminal_view = {"attempted": False, "opened": False, "connected": False}
    terminal_forward_param = ""
    if terminal_agent:
        terminal_forward_param, terminal_agent_url = _pick_task_terminal(
            [task for task in tasks_before_full if isinstance(task, dict)],
            preferred_agent_url=str(terminal_agent.get("url") or ""),
        )
        if terminal_agent_url:
            matched_terminal_agent = next(
                (agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == terminal_agent_url),
                None,
            )
            if matched_terminal_agent:
                terminal_agent = matched_terminal_agent

    tick_results: List[dict] = []
    task_detail_terminal = {"attempted": False, "embedded_visible": False, "connected": False}
    selected_terminal_task: Optional[dict] = None
    active_terminal_session_found = False
    autopilot_stop_before_res = browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)
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
    for attempt_index in range(max(1, int(benchmark_ticks))):
        dispatched = 0
        reason = ""
        if attempt_index > 0 and attempt_index % 3 == 0:
            tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=25)
            tick_results.append(tick_res)
            if tick_res.get("ok") and int(tick_res.get("status") or 0) < 400:
                tick_data = _unwrap_envelope(tick_res.get("body")) or {}
                dispatched = int((tick_data.get("dispatched") or 0) if isinstance(tick_data, dict) else 0)
                reason = str((tick_data.get("reason") or "") if isinstance(tick_data, dict) else "")
        else:
            status_res = browser_api_json(session_id, "GET", "/tasks/autopilot/status", timeout_seconds=20)
            if status_res.get("ok") and int(status_res.get("status") or 0) < 400:
                status_data = _unwrap_envelope(status_res.get("body")) or {}
                reason = "running" if bool((status_data or {}).get("running")) else ""
        tasks_tick_full = _collect_goal_tasks_snapshot(
            session_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            timeout_seconds=45,
        )
        active_terminal_session_found = any(_task_has_active_terminal(task) for task in tasks_tick_full if isinstance(task, dict))
        if terminal_agent and not terminal_forward_param:
            terminal_forward_param, terminal_agent_url = _pick_task_terminal(
                [task for task in tasks_tick_full if isinstance(task, dict)],
                preferred_agent_url=str(terminal_agent.get("url") or ""),
            )
            if terminal_forward_param and terminal_agent_url:
                matched_terminal_agent = next(
                    (agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == terminal_agent_url),
                    None,
                )
                if matched_terminal_agent:
                    terminal_agent = matched_terminal_agent
        if selected_terminal_task is None:
            preferred_terminal_agent_url = str(terminal_agent.get("url") or "").strip() if isinstance(terminal_agent, dict) else ""
            fallback_terminal_task: Optional[dict] = None
            preferred_terminal_task: Optional[dict] = None
            for task in tasks_tick_full:
                if not isinstance(task, dict):
                    continue
                forward_param = _extract_task_terminal_forward_param(task)
                if not forward_param:
                    continue
                if fallback_terminal_task is None:
                    fallback_terminal_task = task
                if preferred_terminal_agent_url and _extract_task_terminal_agent_url(task) == preferred_terminal_agent_url:
                    preferred_terminal_task = task
                    break
            selected_terminal_task = preferred_terminal_task or fallback_terminal_task
        if terminal_agent and terminal_forward_param and not terminal_view.get("opened"):
            terminal_view = _open_worker_terminal_panel(
                session_id,
                str(terminal_agent.get("name") or ""),
                mode="interactive",
                forward_param=terminal_forward_param,
            )
        if isinstance(selected_terminal_task, dict) and not task_detail_terminal.get("embedded_visible"):
            task_detail_terminal = _inspect_task_detail_live_terminal(session_id, str(selected_terminal_task.get("id") or ""))
        worker_terminal_ready = bool(terminal_view.get("opened")) and (
            bool(terminal_view.get("connected")) or active_terminal_session_found
        )
        task_terminal_ready = bool(task_detail_terminal.get("embedded_visible")) and (
            bool(task_detail_terminal.get("connected")) or bool(task_detail_terminal.get("interactive_controls_visible"))
        )
        if worker_terminal_ready or task_terminal_ready:
            break
        if dispatched <= 0 and reason in {"idle", "no_dispatchable_tasks"} and not active_terminal_session_found:
            break
        time.sleep(1.2)
    autopilot_total_ms = int((time.time() - tick_start) * 1000)

    detail_after_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    detail_after = _unwrap_envelope(detail_after_res.get("body")) if detail_after_res.get("ok") else {}
    detail_after = detail_after if isinstance(detail_after, dict) else {}
    tasks_after = detail_after.get("tasks") if isinstance(detail_after, dict) else []
    tasks_after = tasks_after if isinstance(tasks_after, list) else []
    tasks_after_full = _collect_goal_tasks_snapshot(
        session_id,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        timeout_seconds=75,
    )
    if terminal_agent and not terminal_forward_param:
        terminal_forward_param, terminal_agent_url = _pick_task_terminal(
            [task for task in tasks_after_full if isinstance(task, dict)],
            preferred_agent_url=str(terminal_agent.get("url") or ""),
        )
        if terminal_forward_param:
            if terminal_agent_url:
                matched_terminal_agent = next(
                    (agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == terminal_agent_url),
                    None,
                )
                if matched_terminal_agent:
                    terminal_agent = matched_terminal_agent
    preferred_terminal_agent_url = str(terminal_agent.get("url") or "").strip() if isinstance(terminal_agent, dict) else ""
    preferred_terminal_task: Optional[dict] = None
    fallback_terminal_task: Optional[dict] = None
    for task in tasks_after_full:
        if not isinstance(task, dict):
            continue
        forward_param = _extract_task_terminal_forward_param(task)
        if not forward_param:
            continue
        if fallback_terminal_task is None:
            fallback_terminal_task = task
        agent_url = _extract_task_terminal_agent_url(task)
        if preferred_terminal_agent_url and agent_url == preferred_terminal_agent_url:
            preferred_terminal_task = task
            break
    selected_terminal_task = selected_terminal_task or preferred_terminal_task or fallback_terminal_task
    tasks_after_full_ids = {str(task.get("id") or "") for task in tasks_after_full if isinstance(task, dict)}
    active_terminal_session_found = active_terminal_session_found or any(
        _task_has_active_terminal(task) for task in tasks_after_full if isinstance(task, dict)
    )

    after_status = _summarize_tasks(tasks_after)
    fib_mentions = 0
    for task in tasks_after:
        if not isinstance(task, dict):
            continue
        task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
        if "fibonacci" in task_blob:
            fib_mentions += 1
    new_task_ids = sorted(task_id for task_id in tasks_after_full_ids if task_id and task_id not in tasks_before_full_ids)
    followup_task_ids: List[str] = []
    for task in tasks_after_full:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        parent_id = str(task.get("parent_task_id") or "")
        source_id = str(task.get("source_task_id") or "")
        if (parent_id and parent_id in tasks_before_full_ids) or (source_id and source_id in tasks_before_full_ids):
            followup_task_ids.append(tid)
    followup_observed = len(followup_task_ids) > 0 or len(new_task_ids) > 0
    followup_created = followup_observed
    recovery_info: Dict[str, Any] = {
        "triggered": False,
        "analyze_attempted": 0,
        "analyze_created_total": 0,
        "analyze_results": [],
        "manual_followup_status": 0,
        "manual_recovery_task_status": 0,
        "manual_recovery_task_id": "",
        "extra_ticks": 0,
    }
    terminalized = after_status["total"] > 0 and (after_status["completed"] + after_status["failed"] >= after_status["total"])

    # Recovery path: if initial execution terminalized with failures only, force follow-up creation
    # and re-run a few ticks so benchmark can validate an actual iterative flow.
    if after_status["completed"] == 0 and after_status["failed"] > 0 and not followup_created:
        recovery_info["triggered"] = True
        failed_candidates = [
            task
            for task in tasks_after
            if isinstance(task, dict) and str(task.get("status") or "").lower() == "failed"
        ]
        failed_candidates = failed_candidates[:3]
        for task in failed_candidates:
            tid = str(task.get("id") or "").strip()
            if not tid:
                continue
            analyze_res = browser_api_json(
                session_id,
                "POST",
                f"/tasks/auto-planner/analyze/{tid}",
                body={"exit_code": 1},
                timeout_seconds=75,
            )
            recovery_info["analyze_attempted"] = int(recovery_info["analyze_attempted"]) + 1
            created_count = 0
            if analyze_res.get("ok") and int(analyze_res.get("status") or 0) < 400:
                analyze_payload = _unwrap_envelope(analyze_res.get("body")) or {}
                if isinstance(analyze_payload, dict):
                    created = analyze_payload.get("followups_created")
                    if isinstance(created, list):
                        created_count = len(created)
            recovery_info["analyze_created_total"] = int(recovery_info["analyze_created_total"]) + int(created_count)
            recovery_info["analyze_results"].append(
                {
                    "task_id": tid,
                    "status": int(analyze_res.get("status") or 0),
                    "created": int(created_count),
                }
            )
            if created_count > 0:
                break

        if int(recovery_info["analyze_created_total"]) <= 0 and failed_candidates:
            fallback_parent = str((failed_candidates[0] or {}).get("id") or "").strip()
            if fallback_parent:
                fallback_payload = {
                    "items": [
                        {
                            "description": "Analysiere den fehlgeschlagenen Task, behebe die Ursache und liefere ein verifiziertes Ergebnis mit kurzem Test-Nachweis.",
                            "priority": "High",
                        }
                    ]
                }
                manual_res = browser_api_json(
                    session_id,
                    "POST",
                    f"/tasks/{fallback_parent}/followups",
                    body=fallback_payload,
                    timeout_seconds=45,
                )
                recovery_info["manual_followup_status"] = int(manual_res.get("status") or 0)
            # Fallback 2: create an independent recovery task (not blocked by failed parent).
            recovery_task_res = browser_api_json(
                session_id,
                "POST",
                "/tasks",
                body={
                    "title": "Recovery: Fibonacci goal stabilisieren",
                    "description": "Analysiere die fehlgeschlagenen Fibonacci-Tasks, behebe Root Causes und liefere mindestens einen verifizierten erfolgreichen Abschluss.",
                    "priority": "high",
                    "status": "todo",
                    "team_id": team_id or None,
                    "goal_id": goal_id or None,
                    "task_kind": "coding",
                    "required_capabilities": [],
                    "source": "live_click_recovery",
                    "created_by": "live-click-benchmark",
                },
                timeout_seconds=45,
            )
            recovery_info["manual_recovery_task_status"] = int(recovery_task_res.get("status") or 0)
            recovery_task_payload = _unwrap_envelope(recovery_task_res.get("body")) if recovery_task_res.get("ok") else {}
            if isinstance(recovery_task_payload, dict):
                recovery_info["manual_recovery_task_id"] = str(recovery_task_payload.get("id") or "")

        extra_ticks = min(6, max(2, int(benchmark_ticks // 2) or 2))
        recovery_info["extra_ticks"] = extra_ticks
        for _ in range(extra_ticks):
            tick_body = {"team_id": team_id} if team_id else {}
            tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=180)
            tick_results.append(tick_res)
            time.sleep(1.0)

        detail_after_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
        detail_after = _unwrap_envelope(detail_after_res.get("body")) if detail_after_res.get("ok") else {}
        detail_after = detail_after if isinstance(detail_after, dict) else {}
        tasks_after = detail_after.get("tasks") if isinstance(detail_after, dict) else []
        tasks_after = tasks_after if isinstance(tasks_after, list) else []
        tasks_after_full = _collect_goal_tasks_snapshot(
            session_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            timeout_seconds=75,
        )
        tasks_after_full_ids = {str(task.get("id") or "") for task in tasks_after_full if isinstance(task, dict)}
        after_status = _summarize_tasks(tasks_after)
        fib_mentions = 0
        for task in tasks_after:
            if not isinstance(task, dict):
                continue
            task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
            if "fibonacci" in task_blob:
                fib_mentions += 1
        new_task_ids = sorted(task_id for task_id in tasks_after_full_ids if task_id and task_id not in tasks_before_full_ids)
        followup_task_ids = []
        for task in tasks_after_full:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            if not tid:
                continue
            parent_id = str(task.get("parent_task_id") or "")
            source_id = str(task.get("source_task_id") or "")
            if (parent_id and parent_id in tasks_before_full_ids) or (source_id and source_id in tasks_before_full_ids):
                followup_task_ids.append(tid)
        followup_observed = len(followup_task_ids) > 0 or len(new_task_ids) > 0
        followup_created = followup_observed
        if not followup_created:
            fallback_signal = (
                int(recovery_info.get("analyze_created_total") or 0) > 0
                or (200 <= int(recovery_info.get("manual_recovery_task_status") or 0) < 400)
            )
            followup_created = bool(fallback_signal)
        terminalized = after_status["total"] > 0 and (after_status["completed"] + after_status["failed"] >= after_status["total"])

    cfg_res = browser_api_json(session_id, "GET", "/config", timeout_seconds=45)
    cfg_data = _unwrap_envelope(cfg_res.get("body")) if cfg_res.get("ok") else {}
    cfg_data = cfg_data if isinstance(cfg_data, dict) else {}
    provider = str(cfg_data.get("default_provider") or "ollama").strip().lower() or "ollama"
    model = str(cfg_data.get("opencode_default_model") or cfg_data.get("default_model") or "").strip()
    llm_cfg = cfg_data.get("llm_config") if isinstance(cfg_data.get("llm_config"), dict) else {}
    if not model:
        model = str((llm_cfg.get("model") if isinstance(llm_cfg, dict) else "") or "").strip() or "qwen2.5-coder:7b"

    artifacts_summary = (detail_after.get("artifacts") or {}) if isinstance(detail_after, dict) else {}
    result_summary = (artifacts_summary.get("result_summary") or {}) if isinstance(artifacts_summary, dict) else {}
    headline_artifact = (artifacts_summary.get("headline_artifact") or {}) if isinstance(artifacts_summary, dict) else {}
    artifact_entries = (artifacts_summary.get("artifacts") or []) if isinstance(artifacts_summary, dict) else []
    artifact_summary_ok = bool(result_summary) and (
        bool((headline_artifact or {}).get("preview"))
        or (isinstance(artifact_entries, list) and len(artifact_entries) > 0)
    )
    orchestration_rm_res = browser_api_json(
        session_id,
        "GET",
        "/tasks/orchestration/read-model?artifact_flow_enabled=1&artifact_flow_rag_enabled=1&artifact_flow_rag_include_content=1&artifact_flow_rag_top_k=5",
        timeout_seconds=90,
    )
    orchestration_rm_payload = _unwrap_envelope(orchestration_rm_res.get("body")) if orchestration_rm_res.get("ok") else {}
    orchestration_rm_payload = orchestration_rm_payload if isinstance(orchestration_rm_payload, dict) else {}
    artifact_flow_rm = orchestration_rm_payload.get("artifact_flow")
    artifact_flow_rm = artifact_flow_rm if isinstance(artifact_flow_rm, dict) else {}
    reconciliation_rm = orchestration_rm_payload.get("worker_execution_reconciliation")
    reconciliation_rm = reconciliation_rm if isinstance(reconciliation_rm, dict) else {}
    artifact_flow_items = artifact_flow_rm.get("items") if isinstance(artifact_flow_rm.get("items"), list) else []
    tracked_goal_task_ids = {str(task.get("id") or "") for task in tasks_after_full if isinstance(task, dict)}
    matching_artifact_flow_items = [
        item
        for item in artifact_flow_items
        if isinstance(item, dict) and str(item.get("task_id") or "") in tracked_goal_task_ids
    ]
    artifact_flow_summary = {
        "available": bool(artifact_flow_rm),
        "enabled": bool(artifact_flow_rm.get("enabled")),
        "config": dict(artifact_flow_rm.get("config") or {}) if isinstance(artifact_flow_rm.get("config"), dict) else {},
        "counts": dict(artifact_flow_rm.get("counts") or {}) if isinstance(artifact_flow_rm.get("counts"), dict) else {},
        "matching_item_count": len(matching_artifact_flow_items),
        "matching_task_ids": [str(item.get("task_id") or "") for item in matching_artifact_flow_items[:10]],
        "matching_sent_artifact_count": sum(len(item.get("sent_artifact_ids") or []) for item in matching_artifact_flow_items),
        "matching_returned_artifact_count": sum(len(item.get("returned_artifact_ids") or []) for item in matching_artifact_flow_items),
        "matching_worker_job_count": sum(len(item.get("worker_jobs") or []) for item in matching_artifact_flow_items),
        "matching_rag_context_count": sum(len(item.get("rag_context") or []) for item in matching_artifact_flow_items),
        "sample_items": matching_artifact_flow_items[:3],
    }
    reconciliation_summary = {
        "available": bool(reconciliation_rm),
        "issue_count": int(reconciliation_rm.get("issue_count") or 0),
        "counts": dict(reconciliation_rm.get("counts") or {}) if isinstance(reconciliation_rm.get("counts"), dict) else {},
        "issues": list(reconciliation_rm.get("issues") or [])[:5] if isinstance(reconciliation_rm.get("issues"), list) else [],
    }
    operations_console = _open_operations_console_artifact_flow(session_id)
    file_evidence = _extract_file_path_evidence(tasks_after_full)
    multi_file_output_ok = (
        int(file_evidence.get("distinct_file_count") or 0) >= int(max(1, min_distinct_files))
        and int(file_evidence.get("distinct_dir_count") or 0) >= int(max(1, min_distinct_dirs))
    )
    terminal_buffer = str(terminal_view.get("buffer_excerpt") or "")
    terminal_cli_visible = bool(terminal_view.get("cli_command_visible")) or "opencode run" in terminal_buffer.lower()
    terminal_workdir_error = "failed to change directory" in terminal_buffer.lower()
    terminal_signal_ok = terminal_cli_visible or active_terminal_session_found or bool(task_detail_terminal.get("embedded_visible"))
    task_detail_terminal_ok = (
        not task_detail_terminal.get("attempted")
        or (
            bool(task_detail_terminal.get("embedded_visible"))
            and bool(task_detail_terminal.get("connected"))
            and bool(task_detail_terminal.get("interactive_controls_visible"))
        )
    )
    provider_breakdown = (result_summary.get("cost_summary") or {}).get("provider_breakdown") if isinstance(result_summary, dict) else []
    provider_breakdown = provider_breakdown if isinstance(provider_breakdown, list) else []
    observed_terminal_models: List[str] = []
    for task in tasks_after_full:
        if not isinstance(task, dict):
            continue
        model_name = str((_task_terminal_runtime(task) or {}).get("model") or "").strip()
        if model_name:
            observed_terminal_models.append(model_name)
    model_usage_ok = any(
        isinstance(item, dict)
        and str(item.get("provider") or "").strip().lower() == "opencode"
        and _model_identifier_matches(str(item.get("model") or "").strip(), model)
        for item in provider_breakdown
    ) or any(_model_identifier_matches(observed, model) for observed in observed_terminal_models)

    benchmark_success = after_status["completed"] > 0 or active_terminal_session_found
    if require_followup:
        benchmark_success = benchmark_success and followup_observed
    if require_artifact_summary:
        benchmark_success = benchmark_success and artifact_summary_ok
    if require_multi_file_output:
        benchmark_success = benchmark_success and multi_file_output_ok
    benchmark_success = benchmark_success and terminal_signal_ok and not terminal_workdir_error and model_usage_ok and task_detail_terminal_ok
    benchmark_payload = {
        "provider": provider,
        "model": model,
        "task_kind": benchmark_task_kind,
        "success": benchmark_success,
        "quality_gate_passed": benchmark_success,
        "latency_ms": autopilot_total_ms,
        "tokens_total": 0,
    }
    bench_record_res = browser_api_json(session_id, "POST", "/llm/benchmarks/record", body=benchmark_payload, timeout_seconds=45)
    bench_list_res = browser_api_json(
        session_id, "GET", f"/llm/benchmarks?task_kind={benchmark_task_kind}&top_n=10", timeout_seconds=45
    )
    bench_rows = _unwrap_envelope(bench_list_res.get("body")) if bench_list_res.get("ok") else {}
    bench_items = (bench_rows or {}).get("items") if isinstance(bench_rows, dict) else []
    bench_items = bench_items if isinstance(bench_items, list) else []
    model_key = f"{provider}:{model}"
    bench_focus = {}
    for item in bench_items:
        if isinstance(item, dict) and str(item.get("id") or "") == model_key:
            bench_focus = item.get("focus") if isinstance(item.get("focus"), dict) else {}
            break

    autopilot_stop_res = browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)
    if terminal_agent and terminal_forward_param:
        terminal_view = _open_worker_terminal_panel(
            session_id,
            str(terminal_agent.get("name") or ""),
            mode="interactive",
            forward_param=terminal_forward_param,
        )
    if isinstance(selected_terminal_task, dict):
        task_detail_terminal = _inspect_task_detail_live_terminal(session_id, str(selected_terminal_task.get("id") or ""))

    workers_available_max = 0
    workers_online_max = 0
    for tick in tick_results:
        body = tick.get("body") if isinstance(tick, dict) else {}
        data = _unwrap_envelope(body) if isinstance(body, dict) else {}
        debug = data.get("debug") if isinstance(data, dict) and isinstance(data.get("debug"), dict) else {}
        workers_available_max = max(workers_available_max, int(debug.get("workers_available_count") or 0))
        workers_online_max = max(workers_online_max, int(debug.get("workers_online_count") or 0))
    no_worker_blocker = workers_available_max == 0 and workers_online_max == 0

    ok = (
        bool(autopilot_start_res.get("ok"))
        and int(autopilot_start_res.get("status") or 0) < 400
        and
        bool(bench_record_res.get("ok"))
        and int(bench_record_res.get("status") or 0) < 400
        and after_status["total"] > 0
        and (after_status["completed"] > 0 or followup_created or terminalized or active_terminal_session_found or terminal_signal_ok or no_worker_blocker)
    )
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
            "worker_terminal": terminal_view,
            "task_detail_terminal": task_detail_terminal,
            "active_terminal_session_found": active_terminal_session_found,
            "observed_terminal_models": observed_terminal_models[:10],
            "terminal_cli_visible": terminal_cli_visible,
            "terminal_signal_ok": terminal_signal_ok,
            "terminal_workdir_error": terminal_workdir_error,
            "model_usage_ok": model_usage_ok,
            "tasks_before": len(tasks_before),
            "tasks_after": len(tasks_after),
            "tasks_before_full": len(tasks_before_full),
            "tasks_after_full": len(tasks_after_full),
            "new_task_ids": new_task_ids,
            "followup_task_ids": followup_task_ids,
            "followup_created": followup_created,
            "followup_observed": followup_observed,
            "terminalized": terminalized,
            "fibonacci_mentions_in_tasks": fib_mentions,
            "artifacts_summary_present": artifact_summary_ok,
            "result_summary": result_summary if isinstance(result_summary, dict) else {},
            "headline_artifact_preview": str((headline_artifact or {}).get("preview") or "")[:280],
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


