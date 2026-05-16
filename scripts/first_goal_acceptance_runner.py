#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "aborted", "timeout"}
ACTIVE_STATUSES = {"assigned", "proposing", "in_progress", "running"}
BLOCKED_SET = {"todo", "blocked_by_dependency"}


@dataclass
class CriterionResult:
    id: int
    name: str
    passed: bool
    details: str


@dataclass
class RunReport:
    run_index: int
    goal_id: str | None = None
    output_dir: str | None = None
    final_goal_status: str | None = None
    criteria: list[CriterionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.criteria)


class AcceptanceRunner:
    def __init__(self, *, base_url: str, username: str, password: str, timeout_s: int, poll_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.token = self._login()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _get_json_with_retry(self, url: str, *, timeout: int = 20, retries: int = 3) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, headers=self.headers, timeout=timeout)
                resp.raise_for_status()
                return dict(resp.json() or {})
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(min(2.0 * attempt, 5.0))
                    continue
                raise
        if last_exc:
            raise last_exc
        return {}

    def _login(self) -> str:
        resp = requests.post(
            f"{self.base_url}/login",
            json={"username": self.username, "password": self.password},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("login returned empty access token")
        return token

    def _get_goals(self) -> list[dict[str, Any]]:
        payload = self._get_json_with_retry(f"{self.base_url}/goals", timeout=20, retries=3)
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def _get_goal_detail(self, goal_id: str) -> dict[str, Any]:
        payload = self._get_json_with_retry(f"{self.base_url}/goals/{goal_id}/detail", timeout=20, retries=3)
        return dict(payload.get("data") or {})

    def _get_task(self, task_id: str) -> dict[str, Any]:
        payload = self._get_json_with_retry(f"{self.base_url}/tasks/{task_id}", timeout=20, retries=3)
        return dict(payload.get("data") or {})

    def _get_autopilot_status(self) -> dict[str, Any]:
        resp = requests.get(f"{self.base_url}/tasks/autopilot/status", headers=self.headers, timeout=20)
        if resp.status_code >= 400:
            return {}
        return dict(resp.json().get("data") or {})

    def submit_goal(self, *, goal_text: str, output_dir: str, run_trace_id: str) -> tuple[str | None, list[str], Exception | None]:
        before = self._get_goals()
        before_ids = [str(g.get("id")) for g in before if g.get("id")]
        payload = {
            "goal": goal_text,
            "mode": "new_software_project",
            "mode_data": {"project_idea": "RTX3080 eGPU utilization optimization python project"},
            "use_template": False,
            "context": f"acceptance_runner_trace_id={run_trace_id}",
            "execution_preferences": {"output_dir": f"/project-workspaces/{output_dir}"},
        }
        submit_error = None
        response_goal_id: str | None = None
        try:
            # Keep submit timeout short so acceptance observation starts quickly even if
            # goal planning request itself blocks server-side for longer.
            resp = requests.post(f"{self.base_url}/goals", headers=self.headers, json=payload, timeout=12)
            if resp.status_code < 400:
                data = dict(resp.json().get("data") or {})
                goal_obj = dict(data.get("goal") or {})
                gid = str(goal_obj.get("id") or "").strip()
                response_goal_id = gid or None
        except Exception as exc:  # noqa: BLE001
            submit_error = exc

        # Primary resolution: exact response id, then trace marker in context.
        if response_goal_id:
            return response_goal_id, [response_goal_id], submit_error

        deadline = time.time() + 30
        while time.time() < deadline:
            goals = self._get_goals()
            trace_matches = []
            for goal in goals:
                gid = str(goal.get("id") or "").strip()
                context = str(goal.get("context") or "")
                if gid and f"acceptance_runner_trace_id={run_trace_id}" in context:
                    trace_matches.append(gid)
            trace_uniq = sorted(set(trace_matches))
            if len(trace_uniq) == 1:
                return trace_uniq[0], trace_uniq, submit_error
            if len(trace_uniq) > 1:
                return None, trace_uniq, submit_error

            # Fallback: old behavior based on delta set.
            new_ids = [str(g.get("id")) for g in goals if g.get("id") and str(g.get("id")) not in before_ids]
            uniq = sorted(set(new_ids))
            if uniq:
                return (uniq[0] if len(uniq) == 1 else None, uniq, submit_error)
            time.sleep(1.0)
        return None, [], submit_error


def reset_runtime_data() -> None:
    sql = """
begin;
truncate table
  worker_results,
  worker_jobs,
  verification_records,
  retrieval_runs,
  policy_decisions,
  memory_entries,
  context_bundles,
  audit_logs,
  archived_tasks,
  tasks,
  plan_nodes,
  plans,
  goals,
  artifact_versions,
  artifacts
restart identity;
commit;
"""
    subprocess.run(
        [
            "docker",
            "exec",
            "ananta-postgres-1",
            "psql",
            "-U",
            "ananta",
            "-d",
            "ananta",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        check=True,
    )


def run_once(runner: AcceptanceRunner, *, run_index: int, workspace_root: Path, goal_text: str) -> RunReport:
    report = RunReport(run_index=run_index)
    output_dir = f"first-goal-run-{run_index}-{uuid.uuid4().hex[:6]}"
    run_trace_id = f"acc-{run_index}-{uuid.uuid4().hex[:10]}"
    report.output_dir = output_dir
    host_dir = workspace_root / output_dir
    if host_dir.exists():
        shutil.rmtree(host_dir, ignore_errors=True)

    started_at = time.time()
    goal_id, new_ids, submit_error = runner.submit_goal(goal_text=goal_text, output_dir=output_dir, run_trace_id=run_trace_id)
    report.goal_id = goal_id

    # 1 Goal ingestion
    c1_pass = goal_id is not None and len(new_ids) == 1
    c1_details = f"new_goal_ids={new_ids}; submit_error={submit_error}"
    report.criteria.append(CriterionResult(1, "Goal-Ingestion stabil", c1_pass, c1_details))

    if not goal_id:
        # Fill remaining criteria as failed due to missing goal.
        for i, name in [
            (2, "Task-Materialisierung erfolgt automatisch"),
            (3, "Autopilot-Übernahme ohne Eingriff"),
            (4, "Kein Planungs-Deadlock"),
            (5, "Provider-Stabilität ausreichend"),
            (6, "Workspace-Schreibphase erreicht"),
            (7, "Verifikation vorhanden"),
            (8, "Terminaler Goal-Status"),
            (10, "Kein manueller Operatoreingriff"),
        ]:
            report.criteria.append(CriterionResult(i, name, False, "goal not created"))
        return report

    # Observe lifecycle (no manual start/tick)
    status_seen: list[tuple[float, str]] = []
    first_status_time = None
    task_count_at_60 = 0
    first_assigned_at = None
    first_post_assigned_change = False
    deadlock_start = None
    deadlock_violation = False
    cb_open_start = None
    cb_open_violation = False
    workspace_file_seen = False
    verification_seen = False
    latest_tasks: list[dict[str, Any]] = []

    final_status = None
    deadline = started_at + runner.timeout_s
    while time.time() < deadline:
        now = time.time()
        detail = runner._get_goal_detail(goal_id)
        goal = dict(detail.get("goal") or {})
        tasks = list(detail.get("tasks") or [])
        latest_tasks = tasks
        status = str(goal.get("status") or "")
        if status:
            status_seen.append((now, status))
            if first_status_time is None and status in {"planning", "planned"}:
                first_status_time = now
        if now - started_at <= 60:
            task_count_at_60 = max(task_count_at_60, len(tasks))

        # criterion 3 + 4
        task_statuses = [str(t.get("status") or "") for t in tasks]
        assigned_now = any(s == "assigned" for s in task_statuses)
        if assigned_now and first_assigned_at is None:
            first_assigned_at = now
        if first_assigned_at is not None:
            if any(s in {"proposing", "in_progress", "completed", "failed"} for s in task_statuses):
                first_post_assigned_change = True

        non_terminal = [s for s in task_statuses if s and s not in TERMINAL_STATUSES]
        if non_terminal and all(s in BLOCKED_SET for s in non_terminal):
            if deadlock_start is None:
                deadlock_start = now
            elif now - deadlock_start > 120:
                deadlock_violation = True
        else:
            deadlock_start = None

        # criterion 5
        ap = runner._get_autopilot_status()
        open_count = int((ap.get("circuit_breakers") or {}).get("open_count") or 0)
        if open_count > 0:
            if cb_open_start is None:
                cb_open_start = now
            elif now - cb_open_start > 120:
                cb_open_violation = True
        else:
            cb_open_start = None

        # criterion 6
        if host_dir.exists():
            for p in host_dir.rglob("*"):
                if p.is_file():
                    workspace_file_seen = True
                    break

        # criterion 7
        for t in tasks:
            tid = str(t.get("id") or "").strip()
            if not tid:
                continue
            task_detail = runner._get_task(tid)
            last_output = str(task_detail.get("last_output") or "")
            vstat = task_detail.get("verification_status")
            if vstat:
                verification_seen = True
                break
            low = last_output.lower()
            if any(k in low for k in ("pytest", "test", "verification", "smoke", "nicht-ausfuehrbar", "nicht ausführbar")):
                verification_seen = True
                break
        if status in TERMINAL_STATUSES:
            final_status = status
            break
        time.sleep(runner.poll_s)

    report.final_goal_status = final_status or str((runner._get_goal_detail(goal_id).get("goal") or {}).get("status") or "")

    # 1 within 30s planning/planned
    c1b = first_status_time is not None and (first_status_time - started_at) <= 30
    report.criteria[0].passed = report.criteria[0].passed and c1b
    report.criteria[0].details += f"; first_planning_state_at={(first_status_time-started_at) if first_status_time else None:.2f}s" if first_status_time else "; first_planning_state_at=None"

    # 2 task materialization auto
    c2 = task_count_at_60 >= 1
    report.criteria.append(CriterionResult(2, "Task-Materialisierung erfolgt automatisch", c2, f"task_count_within_60s={task_count_at_60}"))

    # 3 autopilot takeover no intervention
    c3 = (first_assigned_at is not None and (first_assigned_at - started_at) <= 90 and first_post_assigned_change)
    report.criteria.append(
        CriterionResult(
            3,
            "Autopilot-Übernahme ohne Eingriff",
            c3,
            f"first_assigned_at={(first_assigned_at-started_at) if first_assigned_at else None}; post_assigned_change={first_post_assigned_change}",
        )
    )

    # 4 no planning deadlock
    c4 = not deadlock_violation
    report.criteria.append(CriterionResult(4, "Kein Planungs-Deadlock", c4, f"deadlock_violation={deadlock_violation}"))

    # 5 provider stability
    c5 = not cb_open_violation
    report.criteria.append(CriterionResult(5, "Provider-Stabilität ausreichend", c5, f"circuit_open_violation={cb_open_violation}"))

    # 6 workspace write phase
    c6 = workspace_file_seen
    report.criteria.append(CriterionResult(6, "Workspace-Schreibphase erreicht", c6, f"workspace={host_dir}; file_seen={workspace_file_seen}"))

    # 7 verification present
    c7 = verification_seen
    report.criteria.append(CriterionResult(7, "Verifikation vorhanden", c7, f"verification_seen={verification_seen}"))

    # 8 terminal goal status in SLA
    c8 = report.final_goal_status in {"completed", "failed"}
    report.criteria.append(CriterionResult(8, "Terminaler Goal-Status", c8, f"final_status={report.final_goal_status}; sla_s={runner.timeout_s}"))

    # 10 no manual operator intervention (guaranteed by runner behavior)
    report.criteria.append(CriterionResult(10, "Kein manueller Operatoreingriff", True, "runner used no manual start/tick/retarget/db edits during run"))

    return report


def aggregate(run_reports: list[RunReport]) -> dict[str, Any]:
    total = len(run_reports)
    completed_runs = sum(1 for r in run_reports if r.final_goal_status == "completed")
    write_phase_runs = sum(1 for r in run_reports if any(c.id == 6 and c.passed for c in r.criteria))
    all_progress_runs = sum(1 for r in run_reports if any(c.id == 3 and c.passed for c in r.criteria))
    return {
        "schema": "first_goal_acceptance.v1",
        "total_runs": total,
        "completed_runs": completed_runs,
        "write_phase_runs": write_phase_runs,
        "autopilot_progress_runs": all_progress_runs,
        "repeatability_pass": (completed_runs >= 2 and write_phase_runs == total),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="First-goal acceptance runner for fresh DB runs")
    p.add_argument("--base-url", default=os.getenv("ANANTA_BASE_URL", "http://localhost:5000"))
    p.add_argument("--user", default=os.getenv("ANANTA_USER", "admin"))
    p.add_argument("--password", default=os.getenv("ANANTA_PASSWORD", "AnantaLocalDevAdmin123!"))
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--sla-seconds", type=int, default=900)
    p.add_argument("--poll-seconds", type=float, default=5.0)
    p.add_argument("--goal-text", default="Create a real multi-file Python project for RTX3080 eGPU utilization optimization; write README, src package, tests, run pytest, store report artifact")
    p.add_argument("--workspace-root", default=str(Path.cwd() / "project-workspaces"))
    p.add_argument("--out", default=str(Path("artifacts") / "first_goal_acceptance_report.json"))
    p.add_argument("--reset-db", action="store_true", help="truncate runtime tables before each run")
    args = p.parse_args()

    Path(args.workspace_root).mkdir(parents=True, exist_ok=True)
    all_reports: list[RunReport] = []

    for i in range(1, max(1, int(args.runs)) + 1):
        if args.reset_db:
            reset_runtime_data()
        runner = AcceptanceRunner(
            base_url=args.base_url,
            username=args.user,
            password=args.password,
            timeout_s=int(args.sla_seconds),
            poll_s=float(args.poll_seconds),
        )
        report = run_once(
            runner,
            run_index=i,
            workspace_root=Path(args.workspace_root),
            goal_text=args.goal_text,
        )
        all_reports.append(report)
        print(f"run {i}: goal={report.goal_id} final={report.final_goal_status} pass={report.passed}")

    summary = aggregate(all_reports)
    payload = {
        "summary": summary,
        "runs": [
            {
                "run_index": r.run_index,
                "goal_id": r.goal_id,
                "output_dir": r.output_dir,
                "final_goal_status": r.final_goal_status,
                "passed": r.passed,
                "criteria": [c.__dict__ for c in r.criteria],
            }
            for r in all_reports
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report: {out_path}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    return 0 if summary["repeatability_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
