#!/usr/bin/env python3
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from firefox_live_click_extended import (
    DEFAULT_REPORT_DIR,
    HUB_BASE,
    HUB_CONTAINER,
    LOGIN_PASS,
    LOGIN_USER,
    _collect_goal_tasks_snapshot,
    _extract_task_terminal_forward_param,
    _inspect_task_detail_live_terminal,
    _task_has_active_terminal,
    _unwrap_envelope,
    browser_api_json,
    current_route_and_title,
    phase_goal,
    phase_setup,
    record_step,
    wd,
    write_report,
)
from test_env_cleanup import cleanup_ollama_runtime, cleanup_test_environment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Slim Firefox live-click test focused on showing the OpenCode terminal after goal submission."
    )
    parser.add_argument(
        "--goal-text",
        default="Implement a small Python Fibonacci helper, add unit tests, and provide a short summary of the changed files and validation.",
        help="Goal text submitted in the auto-planner.",
    )
    parser.add_argument(
        "--goal-wait-seconds",
        type=float,
        default=45.0,
        help="How long to wait for goal submission feedback and initial task creation.",
    )
    parser.add_argument(
        "--terminal-timeout-seconds",
        type=float,
        default=120.0,
        help="How long to wait for a task-bound live terminal with visible OpenCode output.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=1.2,
        help="Delay between focused terminal polling iterations.",
    )
    parser.add_argument(
        "--step-delay-seconds",
        type=float,
        default=0.2,
        help="Small settle delay reused by the shared setup/goal helpers.",
    )
    parser.add_argument(
        "--allow-visible-errors",
        action="store_true",
        help="Collect visible UI errors without failing immediately.",
    )
    parser.add_argument(
        "--skip-created-resource-cleanup",
        action="store_true",
        help="Leave generated goals/resources behind instead of cleaning them up at the end.",
    )
    parser.add_argument(
        "--report-file",
        default="",
        help="Optional JSON report path. Defaults to test-reports/live-click/firefox-live-terminal-focus-<timestamp>.json",
    )
    parser.add_argument(
        "--skip-ollama-runtime-cleanup",
        action="store_true",
        help="Leave any Ollama model loaded after the run instead of unloading it in teardown.",
    )
    return parser


def _goal_detail(session_id: str, goal_id: str) -> Dict[str, Any]:
    if not goal_id:
        return {}
    detail_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    detail_payload = _unwrap_envelope(detail_res.get("body")) if detail_res.get("ok") else {}
    return detail_payload if isinstance(detail_payload, dict) else {}


def _goal_trace_id(detail: Dict[str, Any]) -> str:
    for key in ("goal_trace_id", "trace_id"):
        value = str(detail.get(key) or "").strip()
        if value:
            return value
    goal = detail.get("goal") if isinstance(detail.get("goal"), dict) else {}
    return str(goal.get("trace_id") or goal.get("goal_trace_id") or "").strip()


def _goal_team_id(detail: Dict[str, Any]) -> str:
    goal = detail.get("goal") if isinstance(detail.get("goal"), dict) else {}
    return str(detail.get("team_id") or goal.get("team_id") or "").strip()


def _pick_terminal_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    active_candidate: Optional[Dict[str, Any]] = None
    fallback_candidate: Optional[Dict[str, Any]] = None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not _extract_task_terminal_forward_param(task):
            continue
        if fallback_candidate is None:
            fallback_candidate = task
        if _task_has_active_terminal(task):
            active_candidate = task
            break
    return active_candidate or fallback_candidate


def _ensure_fresh_scrum_team(session_id: str, report: Dict[str, Any]) -> None:
    t0 = time.time()
    team_name = f"Live UI Team {int(time.time())}"
    create_res = browser_api_json(
        session_id,
        "POST",
        "/teams/setup-scrum",
        body={"name": team_name},
        timeout_seconds=45,
    )
    payload = _unwrap_envelope(create_res.get("body")) if create_res.get("ok") else {}
    payload = payload if isinstance(payload, dict) else {}
    team_obj = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    team_id = str(team_obj.get("id") or "").strip()
    cleanup_res: Dict[str, Any] = {}
    archived_cleanup_res: Dict[str, Any] = {}
    if team_id:
        cleanup_res = browser_api_json(
            session_id,
            "POST",
            "/tasks/cleanup",
            body={"mode": "delete", "team_id": team_id},
            timeout_seconds=45,
        )
        archived_cleanup_res = browser_api_json(
            session_id,
            "POST",
            "/tasks/archived/cleanup",
            body={"team_id": team_id},
            timeout_seconds=45,
        )
    if team_id:
        cleanup_targets = report.setdefault("cleanup_targets", {})
        cleanup_targets.setdefault("team_names", []).append(team_name)
        cleanup_targets.setdefault("team_ids", []).append(team_id)
    ok = bool(create_res.get("ok")) and int(create_res.get("status") or 0) < 400 and bool(team_id)
    record_step(
        report,
        "setup",
        "create_fresh_team",
        t0,
        ok,
        {
            "team_name": team_name,
            "team_id": team_id,
            "api_status": int(create_res.get("status") or 0),
            "team_active": bool(team_obj.get("is_active")),
            "task_cleanup_status": int(cleanup_res.get("status") or 0) if cleanup_res else 0,
            "archived_task_cleanup_status": int(archived_cleanup_res.get("status") or 0) if archived_cleanup_res else 0,
            **current_route_and_title(session_id),
        },
    )
    if not ok:
        raise RuntimeError("Could not create fresh scrum team for focused live terminal run")


def phase_terminal_focus(
    session_id: str,
    report: Dict[str, Any],
    *,
    hard_fail: bool,
    terminal_timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    t0 = time.time()
    goal_id = str(report.get("last_goal_id") or "").strip()
    if not goal_id:
        raise RuntimeError("No goal id recorded from goal phase")

    detail = _goal_detail(session_id, goal_id)
    goal_trace_id = _goal_trace_id(detail)
    goal_team_id = _goal_team_id(detail)
    initial_tasks = detail.get("tasks") if isinstance(detail.get("tasks"), list) else []
    if not initial_tasks:
        initial_tasks = _collect_goal_tasks_snapshot(
            session_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            timeout_seconds=60,
        )
    if not initial_tasks:
        record_step(
            report,
            "terminal",
            "wait_for_opencode_terminal",
            t0,
            False,
            {"error": "no_tasks_found_for_goal", "goal_id": goal_id, **current_route_and_title(session_id)},
        )
        raise RuntimeError("No tasks found for goal after submission")

    browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)
    browser_api_json(
        session_id,
        "POST",
        "/tasks/autopilot/start",
        body={"max_concurrency": 2, "security_level": "balanced", "team_id": goal_team_id or None},
        timeout_seconds=45,
    )
    tick_body = {"team_id": goal_team_id} if goal_team_id else {}
    browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=25)

    deadline = time.time() + max(20.0, float(terminal_timeout_seconds))
    selected_task: Optional[Dict[str, Any]] = None
    terminal_snapshot: Dict[str, Any] = {"attempted": False}
    tick_attempts = 1
    active_terminal_seen = False
    cli_command_visible = False

    while time.time() < deadline:
        if tick_attempts % 3 == 0:
            browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=25)
        else:
            browser_api_json(session_id, "GET", "/tasks/autopilot/status", timeout_seconds=20)
        tick_attempts += 1

        tasks = _collect_goal_tasks_snapshot(
            session_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            timeout_seconds=45,
        )
        if not tasks:
            time.sleep(max(0.2, float(poll_interval_seconds)))
            continue

        if any(_task_has_active_terminal(task) for task in tasks):
            active_terminal_seen = True

        selected_task = _pick_terminal_task(tasks) or selected_task
        if selected_task:
            terminal_snapshot = _inspect_task_detail_live_terminal(session_id, str(selected_task.get("id") or ""))
            cli_command_visible = bool(
                re.search(r"opencode\s+run", str(terminal_snapshot.get("buffer_excerpt") or ""), re.IGNORECASE)
            )
            if terminal_snapshot.get("embedded_visible") and terminal_snapshot.get("connected") and cli_command_visible:
                break
        time.sleep(max(0.2, float(poll_interval_seconds)))

    browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)

    task_id = str((selected_task or {}).get("id") or "").strip()
    forward_param = _extract_task_terminal_forward_param(selected_task) if selected_task else ""
    ok = bool(task_id) and bool(forward_param) and active_terminal_seen and bool(
        terminal_snapshot.get("embedded_visible")
    ) and bool(terminal_snapshot.get("connected")) and cli_command_visible
    record_step(
        report,
        "terminal",
        "wait_for_opencode_terminal",
        t0,
        ok,
        {
            "goal_id": goal_id,
            "goal_trace_id": goal_trace_id,
            "goal_team_id": goal_team_id,
            "task_id": task_id,
            "forward_param": forward_param,
            "active_terminal_seen": active_terminal_seen,
            "cli_command_visible": cli_command_visible,
            "terminal_snapshot": terminal_snapshot,
            **current_route_and_title(session_id),
        },
    )
    if not ok:
        raise RuntimeError("Task live terminal did not show connected OpenCode output")


def main() -> None:
    args = build_parser().parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = (
        Path(args.report_file)
        if args.report_file
        else DEFAULT_REPORT_DIR / f"firefox-live-terminal-focus-{ts}.json"
    )
    hard_fail = not args.allow_visible_errors

    report: Dict[str, Any] = {
        "run_id": ts,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "goal_text": str(args.goal_text or "").strip(),
        "hard_fail_visible_errors": hard_fail,
        "steps": [],
        "errors": [],
        "cleanup_targets": {
            "template_names": [],
            "blueprint_names": [],
            "team_names": [],
            "goal_ids": [],
            "team_ids": [],
        },
        "ui_signals": {
            "visible_errors": [],
            "visible_errors_contains_401": False,
            "final_route": "",
            "final_title": "",
        },
    }

    caps = {"capabilities": {"alwaysMatch": {"browserName": "firefox", "acceptInsecureCerts": True}}}
    created = wd("POST", "/session", caps)
    session_id = created.get("sessionId") or (created.get("value") or {}).get("sessionId")
    if not session_id:
        raise RuntimeError(f"No session id returned: {created}")
    report["session_id"] = session_id
    print("session", session_id, flush=True)

    try:
        wd(
            "POST",
            f"/session/{session_id}/timeouts",
            {"script": 180000, "pageLoad": 300000, "implicit": 0},
        )
    except Exception:
        pass

    try:
        print("phase_start", "setup", flush=True)
        phase_setup(
            session_id,
            report,
            hard_fail=hard_fail,
            step_delay_seconds=max(0.0, float(args.step_delay_seconds)),
            bootstrap_setup=False,
        )
        _ensure_fresh_scrum_team(session_id, report)
        print("phase_done", "setup", flush=True)

        print("phase_start", "goal", flush=True)
        phase_goal(
            session_id,
            report,
            hard_fail=hard_fail,
            step_delay_seconds=max(0.0, float(args.step_delay_seconds)),
            goal_wait_seconds=max(20.0, float(args.goal_wait_seconds)),
            goal_text=str(args.goal_text or "").strip(),
        )
        print("phase_done", "goal", flush=True)

        print("phase_start", "terminal", flush=True)
        phase_terminal_focus(
            session_id,
            report,
            hard_fail=hard_fail,
            terminal_timeout_seconds=max(20.0, float(args.terminal_timeout_seconds)),
            poll_interval_seconds=max(0.2, float(args.poll_interval_seconds)),
        )
        print("phase_done", "terminal", flush=True)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append({"message": str(exc)})
        print("run_failed", str(exc), flush=True)
        raise
    finally:
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["ui_signals"].update(current_route_and_title(session_id))
        try:
            wd("DELETE", f"/session/{session_id}")
        except Exception as exc:
            report["errors"].append({"message": f"quit_error: {exc}"})
            print("quit_error", str(exc), flush=True)
        if not args.skip_created_resource_cleanup:
            try:
                report["cleanup"] = cleanup_test_environment(
                    hub_base_url=HUB_BASE,
                    hub_container=HUB_CONTAINER,
                    admin_user=LOGIN_USER,
                    admin_password=LOGIN_PASS,
                    explicit_targets=report.get("cleanup_targets") if isinstance(report.get("cleanup_targets"), dict) else {},
                )
            except Exception as exc:
                report["cleanup"] = {"error": str(exc)}
                report["errors"].append({"message": f"cleanup_error: {exc}"})
                print("cleanup_error", str(exc), flush=True)
        if not args.skip_ollama_runtime_cleanup:
            try:
                report["ollama_cleanup"] = cleanup_ollama_runtime()
            except Exception as exc:
                report["ollama_cleanup"] = {"error": str(exc)}
                report["errors"].append({"message": f"ollama_cleanup_error: {exc}"})
                print("ollama_cleanup_error", str(exc), flush=True)
        write_report(report, report_path)


if __name__ == "__main__":
    main()
