#!/usr/bin/env python3
"""Named steps of the live-click benchmark phase.

``phase_benchmark`` composes these steps; each one owns a single concern
(goal selection, team/worker binding, terminal probing, recovery, summaries)
so the phase itself stays a readable sequence of Hub API interactions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from firefox_automation._benchmark_helpers import (
    _collect_goal_tasks_snapshot,
    _extract_task_terminal_agent_url,
    _extract_task_terminal_forward_param,
    _inspect_task_detail_live_terminal,
    _model_identifier_matches,
    _open_worker_terminal_panel,
    _pick_task_terminal,
    _summarize_tasks,
    _task_has_active_terminal,
    _task_terminal_runtime,
    _unwrap_envelope,
    browser_api_json,
)
from firefox_automation._reporting import record_step

_RECOVERY_FOLLOWUP_DESCRIPTION = (
    "Analysiere den fehlgeschlagenen Task, behebe die Ursache und liefere ein verifiziertes "
    "Ergebnis mit kurzem Test-Nachweis."
)
_RECOVERY_TASK_DESCRIPTION = (
    "Analysiere die fehlgeschlagenen Fibonacci-Tasks, behebe Root Causes und liefere mindestens "
    "einen verifizierten erfolgreichen Abschluss."
)
_ORCHESTRATION_READ_MODEL_PATH = (
    "/tasks/orchestration/read-model?artifact_flow_enabled=1&artifact_flow_rag_enabled=1"
    "&artifact_flow_rag_include_content=1&artifact_flow_rag_top_k=5"
)


def _api_succeeded(response: dict) -> bool:
    return bool(response.get("ok")) and int(response.get("status") or 0) < 400


def _api_body_if_ok(response: dict, default: Any) -> Any:
    return _unwrap_envelope(response.get("body")) if response.get("ok") else default


def _dicts(items: Any) -> List[Dict[str, Any]]:
    return [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]


def task_ids(tasks: List[Any]) -> set[str]:
    return {str(task.get("id") or "") for task in tasks if isinstance(task, dict)}


# --- goal selection -------------------------------------------------------


def load_benchmark_goals(session_id: str, report: dict, t0: float) -> List[Any]:
    goals_res = browser_api_json(session_id, "GET", "/goals", timeout_seconds=45)
    if not goals_res.get("ok") or int(goals_res.get("status") or 0) >= 400:
        record_step(report, "benchmark", "collect_goals", t0, False, {"api": goals_res})
        raise RuntimeError("Could not load goals via browser API")
    goals_payload = _unwrap_envelope(goals_res.get("body"))
    return goals_payload if isinstance(goals_payload, list) else []


def _goal_by_id(goals: List[Any], goal_id: str) -> Optional[dict]:
    for goal in goals:
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            return goal
    return None


def _goal_matching_hint(goals: List[Any], goal_hint: str) -> Optional[dict]:
    for goal in reversed(goals):
        if not isinstance(goal, dict):
            continue
        text_blob = f"{goal.get('goal', '')} {goal.get('summary', '')}".lower()
        if (goal_hint and goal_hint in text_blob) or "fibonacci" in text_blob:
            return goal
    return None


def pick_benchmark_goal(goals: List[Any], report: dict) -> Optional[dict]:
    goal_hint = str((report.get("goal_text") or "")).strip().lower()
    preferred_goal_id = str(report.get("last_goal_id") or "").strip()
    picked_goal = _goal_by_id(goals, preferred_goal_id) if preferred_goal_id else None
    if not picked_goal:
        picked_goal = _goal_matching_hint(goals, goal_hint)
    if not picked_goal and goals:
        picked_goal = goals[-1]
    return picked_goal


def load_goal_detail_before(session_id: str, report: dict, t0: float, goal_id: str) -> dict:
    detail_before_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    if not detail_before_res.get("ok") or int(detail_before_res.get("status") or 0) >= 400:
        record_step(
            report, "benchmark", "goal_detail_before", t0, False, {"api": detail_before_res, "goal_id": goal_id}
        )
        raise RuntimeError("Could not load goal detail before ticks")
    return _unwrap_envelope(detail_before_res.get("body")) or {}


# --- team retargeting and worker binding ---------------------------------


def _expected_team_from_cleanup_targets(report: dict) -> tuple[str, str]:
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
    return expected_team_id, expected_team_name


def _team_matches(item: Any, expected_team_id: str, expected_team_name: str) -> bool:
    return isinstance(item, dict) and (
        (bool(expected_team_id) and str(item.get("id") or "").strip() == expected_team_id)
        or (bool(expected_team_name) and str(item.get("name") or "").strip() == expected_team_name)
    )


def _patch_tasks_to_team(
    session_id: str,
    tasks: List[Any],
    resolved_team_id: str,
    needs_team_retarget: bool,
    task_team_patch_info: Dict[str, Any],
) -> None:
    for task in tasks:
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
        if _api_succeeded(patch_res):
            task_team_patch_info["patched_task_ids"].append(task_id)


def retarget_tasks_to_expected_team(
    session_id: str,
    report: dict,
    team_id: str,
    tasks_before_full: List[Any],
) -> tuple[str, Dict[str, Any]]:
    """Move goal tasks onto the team the setup phase created, if known."""

    task_team_patch_info: Dict[str, Any] = {"resolved_team_id": team_id, "patched_task_ids": [], "patch_statuses": []}
    expected_team_id, expected_team_name = _expected_team_from_cleanup_targets(report)
    if not (expected_team_id or expected_team_name):
        return team_id, task_team_patch_info
    teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
    teams_payload = _api_body_if_ok(teams_res, [])
    teams = teams_payload if isinstance(teams_payload, list) else []
    matched_team = next(
        (item for item in teams if _team_matches(item, expected_team_id, expected_team_name)),
        None,
    )
    resolved_team_id = str((matched_team or {}).get("id") or "").strip()
    if resolved_team_id:
        task_team_patch_info["resolved_team_id"] = resolved_team_id
        needs_team_retarget = not team_id or team_id != resolved_team_id
        _patch_tasks_to_team(session_id, tasks_before_full, resolved_team_id, needs_team_retarget, task_team_patch_info)
        team_id = resolved_team_id
    return team_id, task_team_patch_info


def _team_type_role_ids(session_id: str, team_type_id: str) -> List[str]:
    role_ids: List[str] = []
    type_roles_res = browser_api_json(session_id, "GET", f"/teams/types/{team_type_id}/roles", timeout_seconds=45)
    for item in _dicts(_api_body_if_ok(type_roles_res, [])):
        rid = str(item.get("role_id") or item.get("id") or "")
        if rid and rid not in role_ids:
            role_ids.append(rid)
    return role_ids


def _generic_role_ids(session_id: str) -> List[str]:
    roles_res = browser_api_json(session_id, "GET", "/teams/roles", timeout_seconds=45)
    return [rid for rid in (str(item.get("id") or "") for item in _dicts(_api_body_if_ok(roles_res, []))) if rid]


def _resolve_team_role_ids(session_id: str, team_obj: Dict[str, Any]) -> List[str]:
    role_ids: List[str] = []
    team_type_id = str(team_obj.get("team_type_id") or "")
    if team_type_id:
        role_ids = _team_type_role_ids(session_id, team_type_id)
    if not role_ids:
        role_ids = _generic_role_ids(session_id)
    return role_ids


def _assign_workers_to_team(
    session_id: str,
    team_id: str,
    online_worker_urls: List[str],
    role_ids: List[str],
) -> Dict[str, Any]:
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
    return {
        "applied": bool(patch_res.get("ok")) and int(patch_res.get("status") or 0) < 400,
        "patch_status": int(patch_res.get("status") or 0),
        "activate_status": int(activate_res.get("status") or 0),
        "members_payload": members_payload,
    }


def _is_online_worker(agent: Dict[str, Any]) -> bool:
    return str(agent.get("role") or "").lower() == "worker" and str(agent.get("status") or "").lower() == "online"


@dataclass
class WorkerBinding:
    info: Dict[str, Any]
    agent_entries: List[Dict[str, Any]] = field(default_factory=list)
    team_obj: Optional[Dict[str, Any]] = None


def bind_online_workers_to_team(session_id: str, team_id: str) -> WorkerBinding:
    """Ensure the goal team has online workers assigned, otherwise autopilot stays idle."""

    binding = WorkerBinding(info={"team_id": team_id, "applied": False})
    if not team_id:
        return binding
    agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
    teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
    binding.agent_entries = _dicts(_api_body_if_ok(agents_res, []))
    teams = _api_body_if_ok(teams_res, [])
    teams = teams if isinstance(teams, list) else []
    binding.team_obj = next((t for t in teams if isinstance(t, dict) and str(t.get("id") or "") == team_id), None)
    online_worker_urls = [str(a.get("url") or "") for a in binding.agent_entries if _is_online_worker(a)]
    existing_member_urls = [
        str(m.get("agent_url") or "") for m in ((binding.team_obj or {}).get("members") or []) if isinstance(m, dict)
    ]
    needs_binding = bool(online_worker_urls) and not any(url in existing_member_urls for url in online_worker_urls)
    binding.info.update(
        {
            "online_worker_urls": online_worker_urls,
            "existing_member_urls": existing_member_urls,
            "needs_binding": needs_binding,
        }
    )
    if needs_binding and binding.team_obj:
        role_ids = _resolve_team_role_ids(session_id, binding.team_obj)
        if role_ids:
            binding.info.update(_assign_workers_to_team(session_id, team_id, online_worker_urls, role_ids))
    return binding


def load_agent_entries(session_id: str) -> List[Dict[str, Any]]:
    agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
    return _dicts(_api_body_if_ok(agents_res, []))


def _append_member_urls(members: Any, preferred_worker_urls: List[str]) -> None:
    for member in members or []:
        if not isinstance(member, dict):
            continue
        agent_url = str(member.get("agent_url") or "").strip()
        if agent_url and agent_url not in preferred_worker_urls:
            preferred_worker_urls.append(agent_url)


def preferred_worker_urls(team_obj: Optional[Dict[str, Any]], worker_bind_info: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    if team_obj:
        _append_member_urls(team_obj.get("members"), urls)
    _append_member_urls(worker_bind_info.get("members_payload"), urls)
    return urls


def online_worker_agents(agent_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [agent for agent in agent_entries if _is_online_worker(agent)]


def _agent_with_url(agents: List[Dict[str, Any]], url: str) -> Optional[Dict[str, Any]]:
    return next((agent for agent in agents if str(agent.get("url") or "").strip() == url), None)


def choose_terminal_agent(
    preferred_urls: List[str],
    online_agents: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for worker_url in preferred_urls:
        terminal_agent = _agent_with_url(online_agents, worker_url)
        if terminal_agent:
            return terminal_agent
    return online_agents[0] if online_agents else None


# --- terminal probing -----------------------------------------------------


@dataclass
class TerminalProbe:
    """Mutable terminal observation shared by the tick loop and the summary."""

    agent: Optional[Dict[str, Any]]
    online_agents: List[Dict[str, Any]]
    forward_param: str = ""
    view: Dict[str, Any] = field(default_factory=lambda: {"attempted": False, "opened": False, "connected": False})
    task_detail: Dict[str, Any] = field(
        default_factory=lambda: {"attempted": False, "embedded_visible": False, "connected": False}
    )
    selected_task: Optional[dict] = None
    active_session_found: bool = False

    def refine_from_tasks(self, tasks: List[Any]) -> None:
        """Pick a terminal forward param and prefer the agent that owns it."""

        if not self.agent:
            return
        self.forward_param, terminal_agent_url = _pick_task_terminal(
            [task for task in tasks if isinstance(task, dict)],
            preferred_agent_url=str(self.agent.get("url") or ""),
        )
        if terminal_agent_url:
            matched = _agent_with_url(self.online_agents, terminal_agent_url)
            if matched:
                self.agent = matched

    def preferred_agent_url(self) -> str:
        return str(self.agent.get("url") or "").strip() if isinstance(self.agent, dict) else ""

    def open_worker_panel(self, session_id: str) -> None:
        self.view = _open_worker_terminal_panel(
            session_id,
            str(self.agent.get("name") or ""),
            mode="interactive",
            forward_param=self.forward_param,
        )

    def inspect_selected_task(self, session_id: str) -> None:
        self.task_detail = _inspect_task_detail_live_terminal(session_id, str(self.selected_task.get("id") or ""))

    def ready(self) -> bool:
        worker_terminal_ready = bool(self.view.get("opened")) and (
            bool(self.view.get("connected")) or self.active_session_found
        )
        task_terminal_ready = bool(self.task_detail.get("embedded_visible")) and (
            bool(self.task_detail.get("connected")) or bool(self.task_detail.get("interactive_controls_visible"))
        )
        return worker_terminal_ready or task_terminal_ready


def select_terminal_task(tasks: List[Any], preferred_agent_url: str) -> Optional[dict]:
    """Prefer a terminal-bearing task of the preferred agent, else the first one."""

    fallback_terminal_task: Optional[dict] = None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not _extract_task_terminal_forward_param(task):
            continue
        if fallback_terminal_task is None:
            fallback_terminal_task = task
        if preferred_agent_url and _extract_task_terminal_agent_url(task) == preferred_agent_url:
            return task
    return fallback_terminal_task


def any_active_terminal(tasks: List[Any]) -> bool:
    return any(_task_has_active_terminal(task) for task in tasks if isinstance(task, dict))


# --- autopilot ticks ------------------------------------------------------


def _tick_dispatch_state(
    session_id: str, attempt_index: int, tick_body: dict, tick_results: List[dict]
) -> tuple[int, str]:
    dispatched = 0
    reason = ""
    if attempt_index > 0 and attempt_index % 3 == 0:
        tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=25)
        tick_results.append(tick_res)
        if _api_succeeded(tick_res):
            tick_data = _unwrap_envelope(tick_res.get("body")) or {}
            dispatched = int((tick_data.get("dispatched") or 0) if isinstance(tick_data, dict) else 0)
            reason = str((tick_data.get("reason") or "") if isinstance(tick_data, dict) else "")
    else:
        status_res = browser_api_json(session_id, "GET", "/tasks/autopilot/status", timeout_seconds=20)
        if _api_succeeded(status_res):
            status_data = _unwrap_envelope(status_res.get("body")) or {}
            reason = "running" if bool((status_data or {}).get("running")) else ""
    return dispatched, reason


def _probe_terminals_after_tick(session_id: str, probe: TerminalProbe, tasks_tick_full: List[Any]) -> None:
    probe.active_session_found = any_active_terminal(tasks_tick_full)
    if probe.agent and not probe.forward_param:
        probe.refine_from_tasks(tasks_tick_full)
    if probe.selected_task is None:
        probe.selected_task = select_terminal_task(tasks_tick_full, probe.preferred_agent_url())
    if probe.agent and probe.forward_param and not probe.view.get("opened"):
        probe.open_worker_panel(session_id)
    if isinstance(probe.selected_task, dict) and not probe.task_detail.get("embedded_visible"):
        probe.inspect_selected_task(session_id)


def run_autopilot_tick_loop(
    session_id: str,
    *,
    benchmark_ticks: int,
    tick_body: dict,
    goal_id: str,
    goal_trace_id: str,
    probe: TerminalProbe,
    tick_results: List[dict],
) -> None:
    for attempt_index in range(max(1, int(benchmark_ticks))):
        dispatched, reason = _tick_dispatch_state(session_id, attempt_index, tick_body, tick_results)
        tasks_tick_full = _collect_goal_tasks_snapshot(
            session_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            timeout_seconds=45,
        )
        _probe_terminals_after_tick(session_id, probe, tasks_tick_full)
        if probe.ready():
            break
        if dispatched <= 0 and reason in {"idle", "no_dispatchable_tasks"} and not probe.active_session_found:
            break
        time.sleep(1.2)


# --- goal task observation ------------------------------------------------


@dataclass
class GoalTaskObservation:
    detail_after: Dict[str, Any]
    tasks_after: List[Any]
    tasks_after_full: List[Any]
    after_status: Dict[str, int]
    fib_mentions: int
    new_task_ids: List[str]
    followup_task_ids: List[str]

    @property
    def followup_observed(self) -> bool:
        return len(self.followup_task_ids) > 0 or len(self.new_task_ids) > 0

    @property
    def terminalized(self) -> bool:
        status = self.after_status
        return status["total"] > 0 and (status["completed"] + status["failed"] >= status["total"])


def load_goal_detail_after(session_id: str, goal_id: str) -> tuple[Dict[str, Any], List[Any]]:
    detail_after_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    detail_after = _api_body_if_ok(detail_after_res, {})
    detail_after = detail_after if isinstance(detail_after, dict) else {}
    tasks_after = detail_after.get("tasks") if isinstance(detail_after, dict) else []
    return detail_after, (tasks_after if isinstance(tasks_after, list) else [])


def _fibonacci_mentions(tasks: List[Any]) -> int:
    fib_mentions = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
        if "fibonacci" in task_blob:
            fib_mentions += 1
    return fib_mentions


def _followup_task_ids(tasks: List[Any], before_ids: set[str]) -> List[str]:
    followup_task_ids: List[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        parent_id = str(task.get("parent_task_id") or "")
        source_id = str(task.get("source_task_id") or "")
        if (parent_id and parent_id in before_ids) or (source_id and source_id in before_ids):
            followup_task_ids.append(tid)
    return followup_task_ids


def observe_goal_tasks(
    detail_after: Dict[str, Any],
    tasks_after: List[Any],
    tasks_after_full: List[Any],
    tasks_before_full_ids: set[str],
) -> GoalTaskObservation:
    tasks_after_full_ids = task_ids(tasks_after_full)
    return GoalTaskObservation(
        detail_after=detail_after,
        tasks_after=tasks_after,
        tasks_after_full=tasks_after_full,
        after_status=_summarize_tasks(tasks_after),
        fib_mentions=_fibonacci_mentions(tasks_after),
        new_task_ids=sorted(
            task_id for task_id in tasks_after_full_ids if task_id and task_id not in tasks_before_full_ids
        ),
        followup_task_ids=_followup_task_ids(tasks_after_full, tasks_before_full_ids),
    )


# --- recovery -------------------------------------------------------------


def new_recovery_info() -> Dict[str, Any]:
    return {
        "triggered": False,
        "analyze_attempted": 0,
        "analyze_created_total": 0,
        "analyze_results": [],
        "manual_followup_status": 0,
        "manual_recovery_task_status": 0,
        "manual_recovery_task_id": "",
        "extra_ticks": 0,
    }


def _analyze_created_count(analyze_res: dict) -> int:
    if not _api_succeeded(analyze_res):
        return 0
    analyze_payload = _unwrap_envelope(analyze_res.get("body")) or {}
    if isinstance(analyze_payload, dict):
        created = analyze_payload.get("followups_created")
        if isinstance(created, list):
            return len(created)
    return 0


def _analyze_failed_tasks(session_id: str, failed_candidates: List[dict], recovery_info: Dict[str, Any]) -> None:
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
        created_count = _analyze_created_count(analyze_res)
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


def _create_manual_recovery(
    session_id: str,
    failed_candidates: List[dict],
    team_id: str,
    goal_id: str,
    recovery_info: Dict[str, Any],
) -> None:
    fallback_parent = str((failed_candidates[0] or {}).get("id") or "").strip()
    if fallback_parent:
        fallback_payload = {"items": [{"description": _RECOVERY_FOLLOWUP_DESCRIPTION, "priority": "High"}]}
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
            "description": _RECOVERY_TASK_DESCRIPTION,
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
    recovery_task_payload = _api_body_if_ok(recovery_task_res, {})
    if isinstance(recovery_task_payload, dict):
        recovery_info["manual_recovery_task_id"] = str(recovery_task_payload.get("id") or "")


def _run_extra_ticks(session_id: str, team_id: str, benchmark_ticks: int, tick_results: List[dict]) -> int:
    extra_ticks = min(6, max(2, int(benchmark_ticks // 2) or 2))
    for _ in range(extra_ticks):
        tick_body = {"team_id": team_id} if team_id else {}
        tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=180)
        tick_results.append(tick_res)
        time.sleep(1.0)
    return extra_ticks


def run_failure_recovery(
    session_id: str,
    *,
    tasks_after: List[Any],
    team_id: str,
    goal_id: str,
    benchmark_ticks: int,
    tick_results: List[dict],
    recovery_info: Dict[str, Any],
) -> None:
    """Force follow-up creation when the first run terminalized with failures only."""

    recovery_info["triggered"] = True
    failed_candidates = [
        task for task in tasks_after if isinstance(task, dict) and str(task.get("status") or "").lower() == "failed"
    ][:3]
    _analyze_failed_tasks(session_id, failed_candidates, recovery_info)
    if int(recovery_info["analyze_created_total"]) <= 0 and failed_candidates:
        _create_manual_recovery(session_id, failed_candidates, team_id, goal_id, recovery_info)
    recovery_info["extra_ticks"] = _run_extra_ticks(session_id, team_id, benchmark_ticks, tick_results)


def recovery_fallback_signal(recovery_info: Dict[str, Any]) -> bool:
    return int(recovery_info.get("analyze_created_total") or 0) > 0 or (
        200 <= int(recovery_info.get("manual_recovery_task_status") or 0) < 400
    )


# --- summaries ------------------------------------------------------------


def resolve_provider_and_model(session_id: str) -> tuple[str, str]:
    cfg_res = browser_api_json(session_id, "GET", "/config", timeout_seconds=45)
    cfg_data = _api_body_if_ok(cfg_res, {})
    cfg_data = cfg_data if isinstance(cfg_data, dict) else {}
    provider = str(cfg_data.get("default_provider") or "ollama").strip().lower() or "ollama"
    model = str(cfg_data.get("opencode_default_model") or cfg_data.get("default_model") or "").strip()
    llm_cfg = cfg_data.get("llm_config") if isinstance(cfg_data.get("llm_config"), dict) else {}
    if not model:
        model = str((llm_cfg.get("model") if isinstance(llm_cfg, dict) else "") or "").strip() or "qwen2.5-coder:7b"
    return provider, model


@dataclass(frozen=True)
class ArtifactSummary:
    result_summary: Any
    headline_artifact: Any
    ok: bool


def summarize_goal_artifacts(detail_after: Dict[str, Any]) -> ArtifactSummary:
    artifacts_summary = (detail_after.get("artifacts") or {}) if isinstance(detail_after, dict) else {}
    is_dict = isinstance(artifacts_summary, dict)
    result_summary = (artifacts_summary.get("result_summary") or {}) if is_dict else {}
    headline_artifact = (artifacts_summary.get("headline_artifact") or {}) if is_dict else {}
    artifact_entries = (artifacts_summary.get("artifacts") or []) if is_dict else []
    ok = bool(result_summary) and (
        bool((headline_artifact or {}).get("preview"))
        or (isinstance(artifact_entries, list) and len(artifact_entries) > 0)
    )
    return ArtifactSummary(result_summary=result_summary, headline_artifact=headline_artifact, ok=ok)


def _dict_field(container: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = container.get(key)
    return dict(value or {}) if isinstance(value, dict) else {}


def _artifact_flow_summary(artifact_flow_rm: Dict[str, Any], tasks_after_full: List[Any]) -> Dict[str, Any]:
    artifact_flow_items = artifact_flow_rm.get("items") if isinstance(artifact_flow_rm.get("items"), list) else []
    tracked_goal_task_ids = task_ids(tasks_after_full)
    matching = [
        item
        for item in artifact_flow_items
        if isinstance(item, dict) and str(item.get("task_id") or "") in tracked_goal_task_ids
    ]
    return {
        "available": bool(artifact_flow_rm),
        "enabled": bool(artifact_flow_rm.get("enabled")),
        "config": _dict_field(artifact_flow_rm, "config"),
        "counts": _dict_field(artifact_flow_rm, "counts"),
        "matching_item_count": len(matching),
        "matching_task_ids": [str(item.get("task_id") or "") for item in matching[:10]],
        "matching_sent_artifact_count": sum(len(item.get("sent_artifact_ids") or []) for item in matching),
        "matching_returned_artifact_count": sum(len(item.get("returned_artifact_ids") or []) for item in matching),
        "matching_worker_job_count": sum(len(item.get("worker_jobs") or []) for item in matching),
        "matching_rag_context_count": sum(len(item.get("rag_context") or []) for item in matching),
        "sample_items": matching[:3],
    }


def _reconciliation_summary(reconciliation_rm: Dict[str, Any]) -> Dict[str, Any]:
    issues = reconciliation_rm.get("issues")
    return {
        "available": bool(reconciliation_rm),
        "issue_count": int(reconciliation_rm.get("issue_count") or 0),
        "counts": _dict_field(reconciliation_rm, "counts"),
        "issues": list(issues or [])[:5] if isinstance(issues, list) else [],
    }


def summarize_orchestration_read_model(
    session_id: str,
    tasks_after_full: List[Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    orchestration_rm_res = browser_api_json(session_id, "GET", _ORCHESTRATION_READ_MODEL_PATH, timeout_seconds=90)
    payload = _api_body_if_ok(orchestration_rm_res, {})
    payload = payload if isinstance(payload, dict) else {}
    artifact_flow_rm = payload.get("artifact_flow")
    artifact_flow_rm = artifact_flow_rm if isinstance(artifact_flow_rm, dict) else {}
    reconciliation_rm = payload.get("worker_execution_reconciliation")
    reconciliation_rm = reconciliation_rm if isinstance(reconciliation_rm, dict) else {}
    return _artifact_flow_summary(artifact_flow_rm, tasks_after_full), _reconciliation_summary(reconciliation_rm)


def task_detail_terminal_ok(task_detail_terminal: Dict[str, Any]) -> bool:
    return not task_detail_terminal.get("attempted") or (
        bool(task_detail_terminal.get("embedded_visible"))
        and bool(task_detail_terminal.get("connected"))
        and bool(task_detail_terminal.get("interactive_controls_visible"))
    )


def observed_terminal_models(tasks: List[Any]) -> List[str]:
    models: List[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        model_name = str((_task_terminal_runtime(task) or {}).get("model") or "").strip()
        if model_name:
            models.append(model_name)
    return models


def model_usage_ok(result_summary: Any, observed_models: List[str], model: str) -> bool:
    provider_breakdown = (
        (result_summary.get("cost_summary") or {}).get("provider_breakdown") if isinstance(result_summary, dict) else []
    )
    provider_breakdown = provider_breakdown if isinstance(provider_breakdown, list) else []
    return any(
        isinstance(item, dict)
        and str(item.get("provider") or "").strip().lower() == "opencode"
        and _model_identifier_matches(str(item.get("model") or "").strip(), model)
        for item in provider_breakdown
    ) or any(_model_identifier_matches(observed, model) for observed in observed_models)


def record_model_benchmark(
    session_id: str,
    benchmark_payload: Dict[str, Any],
    benchmark_task_kind: str,
    model_key: str,
) -> tuple[dict, Any]:
    bench_record_res = browser_api_json(
        session_id, "POST", "/llm/benchmarks/record", body=benchmark_payload, timeout_seconds=45
    )
    bench_list_res = browser_api_json(
        session_id, "GET", f"/llm/benchmarks?task_kind={benchmark_task_kind}&top_n=10", timeout_seconds=45
    )
    bench_rows = _api_body_if_ok(bench_list_res, {})
    bench_items = (bench_rows or {}).get("items") if isinstance(bench_rows, dict) else []
    bench_items = bench_items if isinstance(bench_items, list) else []
    bench_focus: Any = {}
    for item in bench_items:
        if isinstance(item, dict) and str(item.get("id") or "") == model_key:
            bench_focus = item.get("focus") if isinstance(item.get("focus"), dict) else {}
            break
    return bench_record_res, bench_focus


def worker_availability(tick_results: List[dict]) -> tuple[int, int]:
    workers_available_max = 0
    workers_online_max = 0
    for tick in tick_results:
        body = tick.get("body") if isinstance(tick, dict) else {}
        data = _unwrap_envelope(body) if isinstance(body, dict) else {}
        debug = data.get("debug") if isinstance(data, dict) and isinstance(data.get("debug"), dict) else {}
        workers_available_max = max(workers_available_max, int(debug.get("workers_available_count") or 0))
        workers_online_max = max(workers_online_max, int(debug.get("workers_online_count") or 0))
    return workers_available_max, workers_online_max
