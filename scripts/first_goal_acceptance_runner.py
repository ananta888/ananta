#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    scenario_id: str | None = None
    scenario_label: str | None = None
    config_mode: str | None = None
    config_profile: str | None = None
    goal_id: str | None = None
    output_dir: str | None = None
    final_goal_status: str | None = None
    config_checksum: str | None = None
    goal_config_source: str | None = None
    effective_config_endpoint_status: int | None = None
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

    def _login(self) -> str:
        resp = requests.post(f"{self.base_url}/login", json={"username": self.username, "password": self.password}, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("login returned empty access token")
        return token

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

    def _post_json_with_retry(self, url: str, *, payload: dict[str, Any], timeout: int = 20, retries: int = 3) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(url, headers=self.headers, json=payload, timeout=timeout)
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

    def get_config(self) -> dict[str, Any]:
        return dict(self._get_json_with_retry(f"{self.base_url}/config").get("data") or {})

    def set_config_patch(self, patch: dict[str, Any]) -> None:
        self._post_json_with_retry(f"{self.base_url}/config", payload=patch, timeout=30)

    def restart_autopilot_unscoped(self) -> None:
        with requests.Session() as session:
            try:
                session.post(f"{self.base_url}/tasks/autopilot/stop", headers=self.headers, timeout=15)
            except Exception:
                pass
            session.post(
                f"{self.base_url}/tasks/autopilot/start",
                headers=self.headers,
                json={"interval_seconds": 1, "max_concurrency": 1},
                timeout=20,
            )

    def _get_goals(self) -> list[dict[str, Any]]:
        payload = self._get_json_with_retry(f"{self.base_url}/goals")
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def _get_goal_detail(self, goal_id: str) -> dict[str, Any]:
        return dict(self._get_json_with_retry(f"{self.base_url}/goals/{goal_id}/detail").get("data") or {})

    def _get_task(self, task_id: str) -> dict[str, Any]:
        return dict(self._get_json_with_retry(f"{self.base_url}/tasks/{task_id}").get("data") or {})

    def _get_autopilot_status(self) -> dict[str, Any]:
        resp = requests.get(f"{self.base_url}/tasks/autopilot/status", headers=self.headers, timeout=20)
        if resp.status_code >= 400:
            return {}
        return dict(resp.json().get("data") or {})

    def get_goal_effective_config(self, goal_id: str) -> tuple[int, dict[str, Any]]:
        resp = requests.get(f"{self.base_url}/goals/{goal_id}/effective-config", headers=self.headers, timeout=20)
        status = int(resp.status_code)
        if status >= 400:
            return status, {}
        return status, dict(resp.json().get("data") or {})

    def submit_goal(
        self,
        *,
        goal_text: str,
        output_dir: str,
        run_trace_id: str,
        config_profile: str | None,
        config_overrides: dict[str, Any] | None,
    ) -> tuple[str | None, list[str], Exception | None]:
        before = self._get_goals()
        before_ids = [str(g.get("id")) for g in before if g.get("id")]
        payload = {
            "goal": goal_text,
            "mode": "new_software_project",
            "mode_data": {"project_idea": "RTX3080 eGPU utilization optimization python project"},
            # LLM-only planning path for acceptance diagnostics.
            "use_template": False,
            "context": f"acceptance_runner_trace_id={run_trace_id}",
            "execution_preferences": {
                "output_dir": f"/project-workspaces/{output_dir}",
                **({"config_profile": config_profile} if config_profile else {}),
                **({"config_overrides": dict(config_overrides or {})} if config_overrides else {}),
            },
        }
        submit_error = None
        response_goal_id: str | None = None
        try:
            resp = requests.post(f"{self.base_url}/goals", headers=self.headers, json=payload, timeout=12)
            if resp.status_code < 400:
                data = dict(resp.json().get("data") or {})
                goal_obj = dict(data.get("goal") or {})
                gid = str(goal_obj.get("id") or "").strip()
                response_goal_id = gid or None
        except Exception as exc:  # noqa: BLE001
            submit_error = exc

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
            new_ids = [str(g.get("id")) for g in goals if g.get("id") and str(g.get("id")) not in before_ids]
            uniq = sorted(set(new_ids))
            if uniq:
                return (uniq[0] if len(uniq) == 1 else None, uniq, submit_error)
            time.sleep(1.0)
        return None, [], submit_error


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base or {})
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(dict(out.get(key) or {}), value)
        else:
            out[key] = value
    return out


def _scenario_definitions(config_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    default_provider = str(config_snapshot.get("default_provider") or "ollama").strip().lower() or "ollama"
    default_model = str(config_snapshot.get("default_model") or "").strip() or None
    opencode_default_model = str(config_snapshot.get("opencode_default_model") or "").strip() or default_model
    local_ollama_model = str(config_snapshot.get("default_model") or "ananta-default:latest").strip() or "ananta-default:latest"

    def _backend_patch(backend: str) -> dict[str, Any]:
        return {
            "sgpt_routing": {
                "task_kind_backend": {
                    "coding": backend,
                    "analysis": backend,
                    "doc": backend,
                    "ops": backend,
                    "research": backend,
                }
            }
        }

    return [
        {
            "id": "opencode_preconfigured",
            "label": "OpenCode Worker + Preconfigured Model",
            "config_profile": "opencode_preconfigured",
            "config_overrides": {},
            "config_patch": _deep_merge(
                _backend_patch("opencode"),
                {"default_provider": default_provider, "default_model": opencode_default_model or default_model},
            ),
        },
        {
            "id": "opencode_ollama_local",
            "label": "OpenCode Worker + Local Ollama",
            "config_profile": "opencode_ollama_local",
            "config_overrides": {},
            "config_patch": _deep_merge(
                _backend_patch("opencode"),
                {
                    "default_provider": "ollama",
                    "default_model": local_ollama_model,
                    "llm_config": {
                        "provider": "ollama",
                        "model": local_ollama_model,
                        "base_url": "http://ollama:11434/api/generate",
                    },
                    "opencode_runtime": {"target_provider": "ollama"},
                },
            ),
        },
        {
            "id": "ananta_ollama_local",
            "label": "Ananta Worker + Local Ollama",
            "config_profile": "ananta_ollama_local",
            "config_overrides": {},
            "config_patch": _deep_merge(
                _backend_patch("ananta-worker"),
                {
                    "default_provider": "ollama",
                    "default_model": local_ollama_model,
                    "llm_config": {
                        "provider": "ollama",
                        "model": local_ollama_model,
                        "base_url": "http://ollama:11434/api/generate",
                    },
                },
            ),
        },
    ]


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
    subprocess.run(["docker", "exec", "ananta-postgres-1", "psql", "-U", "ananta", "-d", "ananta", "-v", "ON_ERROR_STOP=1", "-c", sql], check=True)


def run_once(
    runner: AcceptanceRunner,
    *,
    run_index: int,
    workspace_root: Path,
    goal_text: str,
    scenario_id: str | None,
    scenario_label: str | None,
    config_mode: str,
    config_profile: str | None,
    config_overrides: dict[str, Any] | None,
) -> RunReport:
    report = RunReport(
        run_index=run_index,
        scenario_id=scenario_id,
        scenario_label=scenario_label,
        config_mode=config_mode,
        config_profile=config_profile,
    )
    output_dir = f"first-goal-run-{run_index}-{uuid.uuid4().hex[:6]}"
    run_trace_id = f"acc-{run_index}-{uuid.uuid4().hex[:10]}"
    report.output_dir = output_dir
    host_dir = workspace_root / output_dir
    if host_dir.exists():
        shutil.rmtree(host_dir, ignore_errors=True)

    started_at = time.time()
    goal_id, new_ids, submit_error = runner.submit_goal(
        goal_text=goal_text,
        output_dir=output_dir,
        run_trace_id=run_trace_id,
        config_profile=config_profile if config_mode == "goal_scoped" else None,
        config_overrides=config_overrides if config_mode == "goal_scoped" else None,
    )
    report.goal_id = goal_id

    c1_pass = goal_id is not None and len(new_ids) == 1
    report.criteria.append(CriterionResult(1, "Goal-Ingestion stabil", c1_pass, f"new_goal_ids={new_ids}; submit_error={submit_error}"))

    if not goal_id:
        for i, name in [(2, "Task-Materialisierung erfolgt automatisch"), (3, "Autopilot-Übernahme ohne Eingriff"), (4, "Kein Planungs-Deadlock"), (5, "Provider-Stabilität ausreichend"), (6, "Workspace-Schreibphase erreicht"), (7, "Verifikation vorhanden"), (8, "Terminaler Goal-Status"), (10, "Kein manueller Operatoreingriff")]:
            report.criteria.append(CriterionResult(i, name, False, "goal not created"))
        return report

    status_seen: list[tuple[float, str]] = []
    first_status_time = None
    task_count_at_60 = 0
    first_task_seen_at = None
    first_assigned_at = None
    first_post_assigned_change = False
    deadlock_start = None
    deadlock_violation = False
    cb_open_start = None
    cb_open_violation = False
    workspace_file_seen = False
    verification_seen = False
    max_idle_stretch_s = 0.0
    idle_hang_violation = False
    last_activity_at = started_at
    prev_status = None
    prev_task_count = 0
    planning_extended_by_activity = False
    planning_real_llm_seen = False
    planning_synthetic_llm_seen = False

    final_status = None
    deadline = started_at + runner.timeout_s
    while time.time() < deadline:
        now = time.time()
        detail = runner._get_goal_detail(goal_id)
        goal = dict(detail.get("goal") or {})
        tasks = list(detail.get("tasks") or [])
        status = str(goal.get("status") or "")
        if status:
            status_seen.append((now, status))
            if first_status_time is None and status in {"planning", "planned"}:
                first_status_time = now
            if prev_status is None or status != prev_status:
                last_activity_at = now
            prev_status = status
        if now - started_at <= 60:
            task_count_at_60 = max(task_count_at_60, len(tasks))
        if first_task_seen_at is None and len(tasks) > 0:
            first_task_seen_at = now
            last_activity_at = now
        if len(tasks) != prev_task_count:
            last_activity_at = now
            prev_task_count = len(tasks)

        task_statuses = [str(t.get("status") or "") for t in tasks]
        assigned_now = any(s == "assigned" for s in task_statuses)
        if assigned_now and first_assigned_at is None:
            first_assigned_at = now
            last_activity_at = now
        if first_assigned_at is not None and any(s in {"proposing", "in_progress", "completed", "failed"} for s in task_statuses):
            first_post_assigned_change = True
            last_activity_at = now

        non_terminal = [s for s in task_statuses if s and s not in TERMINAL_STATUSES]
        if non_terminal and all(s in BLOCKED_SET for s in non_terminal):
            if deadlock_start is None:
                deadlock_start = now
            elif now - deadlock_start > 120:
                deadlock_violation = True
        else:
            deadlock_start = None

        ap = runner._get_autopilot_status()
        open_count = int((ap.get("circuit_breakers") or {}).get("open_count") or 0)
        if open_count > 0:
            if cb_open_start is None:
                cb_open_start = now
            elif now - cb_open_start > 120:
                cb_open_violation = True
        else:
            cb_open_start = None

        if host_dir.exists() and any(p.is_file() for p in host_dir.rglob("*")):
            workspace_file_seen = True

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
            if any(k in last_output.lower() for k in ("pytest", "test", "verification", "smoke", "nicht-ausfuehrbar", "nicht ausführbar")):
                verification_seen = True
                break
            proposal = dict(task_detail.get("last_proposal") or {})
            cli_result = dict(proposal.get("cli_result") or {})
            prof_entries = list(cli_result.get("llm_call_profile") or [])
            if prof_entries:
                last_activity_at = now
            for entry in prof_entries:
                if not isinstance(entry, dict):
                    continue
                est = bool(entry.get("estimated"))
                src = str(entry.get("source") or "").strip()
                if est or src == "orchestrator_synthetic":
                    planning_synthetic_llm_seen = True
                else:
                    planning_real_llm_seen = True

        idle_s = now - last_activity_at
        max_idle_stretch_s = max(max_idle_stretch_s, idle_s)
        if status in {"planning", "planned"} and idle_s > 120:
            idle_hang_violation = True
        if status in {"planning", "planned"} and (now - started_at) > 60 and (planning_real_llm_seen or first_task_seen_at is not None):
            planning_extended_by_activity = True
        if status in TERMINAL_STATUSES:
            final_status = status
            break
        time.sleep(runner.poll_s)

    report.final_goal_status = final_status or str((runner._get_goal_detail(goal_id).get("goal") or {}).get("status") or "")

    c1b = first_status_time is not None and (first_status_time - started_at) <= 30
    report.criteria[0].passed = report.criteria[0].passed and c1b
    if first_status_time is not None:
        report.criteria[0].details += f"; first_planning_state_at={first_status_time-started_at:.2f}s"

    c2 = (task_count_at_60 >= 1) or ((first_task_seen_at is not None) and planning_extended_by_activity and not idle_hang_violation)
    report.criteria.append(CriterionResult(2, "Task-Materialisierung erfolgt automatisch", c2, f"task_count_within_60s={task_count_at_60}; first_task_seen_at={(first_task_seen_at-started_at) if first_task_seen_at else None}; planning_extended_by_activity={planning_extended_by_activity}; idle_hang_violation={idle_hang_violation}; max_idle_stretch_s={max_idle_stretch_s:.1f}"))

    c3_fast = first_assigned_at is not None and (first_assigned_at - started_at) <= 90 and first_post_assigned_change
    c3_slow = first_assigned_at is not None and first_post_assigned_change and not idle_hang_violation and (planning_real_llm_seen or planning_synthetic_llm_seen)
    report.criteria.append(CriterionResult(3, "Autopilot-Übernahme ohne Eingriff", c3_fast or c3_slow, f"first_assigned_at={(first_assigned_at-started_at) if first_assigned_at else None}; post_assigned_change={first_post_assigned_change}; planning_real_llm_seen={planning_real_llm_seen}; planning_synthetic_llm_seen={planning_synthetic_llm_seen}; idle_hang_violation={idle_hang_violation}"))
    report.criteria.append(CriterionResult(4, "Kein Planungs-Deadlock", not deadlock_violation, f"deadlock_violation={deadlock_violation}"))
    report.criteria.append(CriterionResult(5, "Provider-Stabilität ausreichend", not cb_open_violation, f"circuit_open_violation={cb_open_violation}"))
    report.criteria.append(CriterionResult(6, "Workspace-Schreibphase erreicht", workspace_file_seen, f"workspace={host_dir}; file_seen={workspace_file_seen}"))
    report.criteria.append(CriterionResult(7, "Verifikation vorhanden", verification_seen, f"verification_seen={verification_seen}"))
    report.criteria.append(CriterionResult(8, "Terminaler Goal-Status", report.final_goal_status in {"completed", "failed"}, f"final_status={report.final_goal_status}; sla_s={runner.timeout_s}"))
    report.criteria.append(CriterionResult(10, "Kein manueller Operatoreingriff", True, "runner used no manual start/tick/retarget/db edits during run"))

    cfg_status, cfg_payload = runner.get_goal_effective_config(goal_id)
    report.effective_config_endpoint_status = cfg_status
    report.config_checksum = str(cfg_payload.get("config_checksum") or "").strip() or None
    report.goal_config_source = str(cfg_payload.get("goal_config_source") or "").strip() or None

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
    p.add_argument("--scenario-repeats", type=int, default=1)
    p.add_argument("--parallel-goals-per-scenario", type=int, default=1)
    p.add_argument("--allow-unsafe-global-parallel", action="store_true")
    p.add_argument("--config-mode", choices=["legacy_global_config", "goal_scoped"], default="goal_scoped")
    p.add_argument("--sla-seconds", type=int, default=900)
    p.add_argument("--poll-seconds", type=float, default=5.0)
    p.add_argument("--goal-text", default="Create a real multi-file Python project for RTX3080 eGPU utilization optimization; write README, src package, tests, run pytest, store report artifact")
    p.add_argument("--workspace-root", default=str(Path.cwd() / "project-workspaces"))
    p.add_argument("--out", default=str(Path("artifacts") / "first_goal_acceptance_report.json"))
    p.add_argument("--reset-db", action="store_true")
    args = p.parse_args()

    if int(args.parallel_goals_per_scenario) > 1 and args.config_mode == "legacy_global_config" and not args.allow_unsafe_global_parallel:
        raise SystemExit("parallel mode is blocked for legacy_global_config unless --allow-unsafe-global-parallel is set")

    Path(args.workspace_root).mkdir(parents=True, exist_ok=True)
    all_reports: list[RunReport] = []

    runner = AcceptanceRunner(base_url=args.base_url, username=args.user, password=args.password, timeout_s=int(args.sla_seconds), poll_s=float(args.poll_seconds))
    baseline_cfg = runner.get_config()
    scenarios = _scenario_definitions(baseline_cfg)
    scenario_repeats = max(1, int(args.scenario_repeats))

    expanded_runs: list[dict[str, Any]] = []
    if scenario_repeats <= 1:
        for i in range(1, max(1, int(args.runs)) + 1):
            expanded_runs.append({"run_index": i, "scenario": scenarios[0]})
    else:
        idx = 1
        for _rep in range(scenario_repeats):
            for scenario in scenarios:
                expanded_runs.append({"run_index": idx, "scenario": scenario})
                idx += 1

    for item in expanded_runs:
        i = int(item["run_index"])
        scenario = dict(item["scenario"] or {})
        if args.reset_db:
            reset_runtime_data()

        if args.config_mode == "legacy_global_config":
            runner.set_config_patch(dict(scenario.get("config_patch") or {}))
            runner.restart_autopilot_unscoped()

        parallel_n = max(1, int(args.parallel_goals_per_scenario))
        if parallel_n == 1:
            report = run_once(
                runner,
                run_index=i,
                workspace_root=Path(args.workspace_root),
                goal_text=args.goal_text,
                scenario_id=str(scenario.get("id") or ""),
                scenario_label=str(scenario.get("label") or ""),
                config_mode=args.config_mode,
                config_profile=str(scenario.get("config_profile") or "") or None,
                config_overrides=dict(scenario.get("config_overrides") or {}),
            )
            all_reports.append(report)
            print(f"run {i} [{report.scenario_id}]: goal={report.goal_id} final={report.final_goal_status} pass={report.passed}")
            continue

        def _parallel_worker(slot: int) -> RunReport:
            return run_once(
                runner,
                run_index=(i * 1000) + slot,
                workspace_root=Path(args.workspace_root),
                goal_text=args.goal_text,
                scenario_id=str(scenario.get("id") or ""),
                scenario_label=f"{str(scenario.get('label') or '')} [parallel-{slot}]",
                config_mode=args.config_mode,
                config_profile=str(scenario.get("config_profile") or "") or None,
                config_overrides=dict(scenario.get("config_overrides") or {}),
            )

        with ThreadPoolExecutor(max_workers=parallel_n) as ex:
            futures = [ex.submit(_parallel_worker, slot) for slot in range(1, parallel_n + 1)]
            for fut in as_completed(futures):
                report = fut.result()
                all_reports.append(report)
                print(f"run {report.run_index} [{report.scenario_id}]: goal={report.goal_id} final={report.final_goal_status} pass={report.passed}")

    if args.config_mode == "legacy_global_config":
        try:
            runner.set_config_patch(baseline_cfg)
            runner.restart_autopilot_unscoped()
        except Exception:
            pass

    summary = aggregate(all_reports)
    payload = {
        "summary": summary,
        "runs": [
            {
                "run_index": r.run_index,
                "scenario_id": r.scenario_id,
                "scenario_label": r.scenario_label,
                "config_mode": r.config_mode,
                "config_profile": r.config_profile,
                "goal_id": r.goal_id,
                "output_dir": r.output_dir,
                "final_goal_status": r.final_goal_status,
                "config_checksum": r.config_checksum,
                "goal_config_source": r.goal_config_source,
                "effective_config_endpoint_status": r.effective_config_endpoint_status,
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
