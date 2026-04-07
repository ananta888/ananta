#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib import request

DEFAULT_HUB_BASE_URL = os.getenv("HUB_BASE_URL", "http://127.0.0.1:5000")
DEFAULT_HUB_CONTAINER = os.getenv("ANANTA_HUB_CONTAINER", "ananta-ai-agent-hub-1")
DEFAULT_OLLAMA_CONTAINER = os.getenv("ANANTA_OLLAMA_CONTAINER", "ollama")
DEFAULT_LIVE_PREFIXES = {
    "template_names": ["Live UI Template "],
    "blueprint_names": ["Live UI Blueprint "],
    "team_names": ["Live UI Team "],
}


def _read_repo_dotenv() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _read_repo_dotenv()


def _env_value(name: str, default: str = "") -> str:
    return str(os.getenv(name) or _DOTENV.get(name) or default).strip()


def req(base: str, method: str, path: str, body: Any | None = None, token: str | None = None, timeout: int = 60) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_obj = request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    with request.urlopen(req_obj, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def login_token(base: str, username: str, password: str) -> str:
    payload = req(base, "POST", "/login", {"username": username, "password": password}, timeout=45)
    token = str((payload.get("data") or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("login_failed_no_access_token")
    return token


def _docker_exec_hub_python(container_name: str, script_body: str) -> str:
    last_error: subprocess.CalledProcessError | None = None
    for python_bin in ("python3", "python"):
        cmd = ["docker", "exec", container_name, python_bin, "-c", script_body]
        try:
            res = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return (res.stdout or "").strip()
        except subprocess.CalledProcessError as exc:
            last_error = exc
            stderr = str(exc.stderr or "")
            stdout = str(exc.stdout or "")
            if "executable file not found" not in stderr and "not found" not in stderr and "not found" not in stdout:
                raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("docker_exec_python_unavailable")


def _run_command(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _docker_inspect_value(container_name: str, template: str, *, timeout: int = 15) -> str:
    res = _run_command(["docker", "inspect", "-f", template, container_name], timeout=timeout)
    if res.returncode != 0:
        return ""
    return str(res.stdout or "").strip()


def _list_loaded_ollama_models(container_name: str) -> list[str]:
    res = _run_command(["docker", "exec", container_name, "ollama", "ps"], timeout=20)
    if res.returncode != 0:
        return []
    lines = [line.strip() for line in str(res.stdout or "").splitlines() if line.strip()]
    if not lines:
        return []
    models: list[str] = []
    for line in lines[1:]:
        name = line.split(None, 1)[0].strip()
        if name:
            models.append(name)
    return _unique_strings(models)


def cleanup_ollama_runtime(
    *,
    ollama_container: str = DEFAULT_OLLAMA_CONTAINER,
    stop_timeout_seconds: int = 10,
    health_timeout_seconds: int = 60,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "container": ollama_container,
        "before": [],
        "stopped": [],
        "after_stop": [],
        "restarted": False,
        "health": "",
        "after_restart": [],
        "errors": [],
    }
    summary["before"] = _list_loaded_ollama_models(ollama_container)
    if not summary["before"]:
        summary["health"] = _docker_inspect_value(ollama_container, "{{.State.Health.Status}}") or ""
        return summary

    for model_name in summary["before"]:
        try:
            res = _run_command(
                ["docker", "exec", ollama_container, "ollama", "stop", model_name],
                timeout=max(1, int(stop_timeout_seconds)),
            )
            summary["stopped"].append(
                {
                    "model": model_name,
                    "exit_code": int(res.returncode),
                    "stdout": str(res.stdout or "").strip(),
                    "stderr": str(res.stderr or "").strip(),
                }
            )
        except subprocess.TimeoutExpired:
            summary["stopped"].append({"model": model_name, "timeout": True})
            summary["errors"].append(f"stop_timeout:{model_name}")

    summary["after_stop"] = _list_loaded_ollama_models(ollama_container)
    if summary["after_stop"]:
        res = _run_command(["docker", "restart", ollama_container], timeout=max(20, int(health_timeout_seconds)))
        summary["restarted"] = res.returncode == 0
        if res.returncode != 0:
            summary["errors"].append(str(res.stderr or res.stdout or "ollama_restart_failed").strip())
        deadline = time.monotonic() + max(1.0, float(health_timeout_seconds))
        while time.monotonic() < deadline:
            health = _docker_inspect_value(ollama_container, "{{.State.Health.Status}}")
            if health:
                summary["health"] = health
            if health == "healthy":
                break
            running = _docker_inspect_value(ollama_container, "{{.State.Running}}")
            if running == "false":
                break
            time.sleep(2)

    summary["health"] = summary["health"] or _docker_inspect_value(ollama_container, "{{.State.Health.Status}}") or ""
    summary["after_restart"] = _list_loaded_ollama_models(ollama_container)
    return summary


def get_admin_from_hub_container(container_name: str) -> tuple[str, str]:
    out = _docker_exec_hub_python(
        container_name,
        "import os;print(os.getenv('INITIAL_ADMIN_USER','admin'));print(os.getenv('INITIAL_ADMIN_PASSWORD',''))",
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    user = lines[0] if lines else "admin"
    password = lines[1] if len(lines) > 1 else ""
    return user, password


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload.get("data")
    return payload


def _unique_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _list_resources(base: str, token: str, path: str) -> list[dict[str, Any]]:
    payload = _unwrap(req(base, "GET", path, token=token, timeout=60))
    return [item for item in (payload if isinstance(payload, list) else []) if isinstance(item, dict)]


def _match_resources(
    items: list[dict[str, Any]],
    *,
    exact_names: list[str] | None = None,
    prefixes: list[str] | None = None,
) -> list[dict[str, Any]]:
    exact = {str(name).strip() for name in (exact_names or []) if str(name).strip()}
    pref = [str(prefix).strip() for prefix in (prefixes or []) if str(prefix).strip()]
    matched: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if name in exact or any(name.startswith(prefix) for prefix in pref):
            matched.append(item)
    return matched


def _extract_targets_from_report(report_path: Path) -> dict[str, list[str]]:
    result = {
        "template_names": [],
        "blueprint_names": [],
        "team_names": [],
        "goal_ids": [],
        "team_ids": [],
    }
    if not report_path.exists():
        return result
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    if isinstance(raw.get("last_goal_id"), str) and raw.get("last_goal_id"):
        result["goal_ids"].append(raw["last_goal_id"])
    for step in raw.get("steps") or []:
        if not isinstance(step, dict):
            continue
        details = step.get("details") or {}
        if not isinstance(details, dict):
            continue
        if step.get("phase") == "setup" and step.get("step") == "template_create":
            result["template_names"].append(str(details.get("template_name") or ""))
        if step.get("phase") == "setup" and step.get("step") == "blueprint_team_create":
            result["blueprint_names"].append(str(details.get("blueprint_name") or ""))
            result["team_names"].append(str(details.get("team_name") or ""))
        if step.get("phase") == "goal" and step.get("step") == "goal_plan_submit":
            result["goal_ids"].append(str(details.get("created_goal_id") or ""))
        if step.get("phase") == "benchmark":
            worker_bind = details.get("worker_bind_info") or {}
            if isinstance(worker_bind, dict):
                result["team_ids"].append(str(worker_bind.get("team_id") or ""))
    return {key: _unique_strings(values) for key, values in result.items()}


def _cleanup_goal_data_in_hub(hub_container: str, goal_ids: list[str], team_ids: list[str]) -> dict[str, Any]:
    payload = json.dumps({"goal_ids": _unique_strings(goal_ids), "team_ids": _unique_strings(team_ids)}, ensure_ascii=True)
    script = f"""
import json
from sqlmodel import Session, select, delete
from agent.database import engine
from agent.db_models import (
    ArchivedTaskDB,
    ContextBundleDB,
    GoalDB,
    MemoryEntryDB,
    PlanDB,
    PlanNodeDB,
    PolicyDecisionDB,
    RetrievalRunDB,
    TaskDB,
    VerificationRecordDB,
    WorkerJobDB,
    WorkerResultDB,
)

payload = json.loads({json.dumps(payload)})
goal_ids = [item for item in payload.get("goal_ids", []) if item]
team_ids = [item for item in payload.get("team_ids", []) if item]

deleted = {{}}

with Session(engine) as session:
    resolved_goal_ids = set(goal_ids)
    if team_ids:
        for goal in session.exec(select(GoalDB).where(GoalDB.team_id.in_(team_ids))).all():
            resolved_goal_ids.add(goal.id)
    resolved_goal_ids = sorted(item for item in resolved_goal_ids if item)

    task_ids = set()
    archived_task_ids = set()
    plan_ids = set()
    retrieval_run_ids = set()
    worker_job_ids = set()

    if resolved_goal_ids:
        for task in session.exec(select(TaskDB).where(TaskDB.goal_id.in_(resolved_goal_ids))).all():
            task_ids.add(task.id)
            if task.plan_id:
                plan_ids.add(task.plan_id)
        for task in session.exec(select(ArchivedTaskDB).where(ArchivedTaskDB.goal_id.in_(resolved_goal_ids))).all():
            archived_task_ids.add(task.id)
            if task.plan_id:
                plan_ids.add(task.plan_id)
        for plan in session.exec(select(PlanDB).where(PlanDB.goal_id.in_(resolved_goal_ids))).all():
            plan_ids.add(plan.id)
    if team_ids:
        for task in session.exec(select(TaskDB).where(TaskDB.team_id.in_(team_ids))).all():
            task_ids.add(task.id)
            if task.goal_id:
                resolved_goal_ids.append(task.goal_id)
            if task.plan_id:
                plan_ids.add(task.plan_id)
        for task in session.exec(select(ArchivedTaskDB).where(ArchivedTaskDB.team_id.in_(team_ids))).all():
            archived_task_ids.add(task.id)
            if task.goal_id:
                resolved_goal_ids.append(task.goal_id)
            if task.plan_id:
                plan_ids.add(task.plan_id)
    resolved_goal_ids = sorted(set(item for item in resolved_goal_ids if item))

    all_task_ids = sorted(task_ids | archived_task_ids)
    if all_task_ids:
        for job_id in session.exec(
            select(WorkerJobDB.id).where(
                (WorkerJobDB.parent_task_id.in_(all_task_ids)) | (WorkerJobDB.subtask_id.in_(all_task_ids))
            )
        ).all():
            worker_job_ids.add(job_id)
        for run_id in session.exec(select(RetrievalRunDB.id).where(RetrievalRunDB.task_id.in_(all_task_ids))).all():
            retrieval_run_ids.add(run_id)
    if resolved_goal_ids:
        for run_id in session.exec(select(RetrievalRunDB.id).where(RetrievalRunDB.goal_id.in_(resolved_goal_ids))).all():
            retrieval_run_ids.add(run_id)

    if worker_job_ids:
        session.exec(delete(WorkerResultDB).where(WorkerResultDB.worker_job_id.in_(sorted(worker_job_ids))))
        session.exec(delete(WorkerJobDB).where(WorkerJobDB.id.in_(sorted(worker_job_ids))))
    if all_task_ids:
        session.exec(delete(WorkerResultDB).where(WorkerResultDB.task_id.in_(all_task_ids)))
        session.exec(delete(ContextBundleDB).where(ContextBundleDB.task_id.in_(all_task_ids)))
        session.exec(delete(TaskDB).where(TaskDB.id.in_(all_task_ids)))
        session.exec(delete(ArchivedTaskDB).where(ArchivedTaskDB.id.in_(all_task_ids)))
        session.exec(delete(VerificationRecordDB).where(VerificationRecordDB.task_id.in_(all_task_ids)))
        session.exec(delete(PolicyDecisionDB).where(PolicyDecisionDB.task_id.in_(all_task_ids)))
        session.exec(delete(MemoryEntryDB).where(MemoryEntryDB.task_id.in_(all_task_ids)))
    if retrieval_run_ids:
        session.exec(delete(ContextBundleDB).where(ContextBundleDB.retrieval_run_id.in_(sorted(retrieval_run_ids))))
        session.exec(delete(RetrievalRunDB).where(RetrievalRunDB.id.in_(sorted(retrieval_run_ids))))
    if plan_ids:
        session.exec(delete(PlanNodeDB).where(PlanNodeDB.plan_id.in_(sorted(plan_ids))))
        session.exec(delete(PlanDB).where(PlanDB.id.in_(sorted(plan_ids))))
    if resolved_goal_ids:
        session.exec(delete(VerificationRecordDB).where(VerificationRecordDB.goal_id.in_(resolved_goal_ids)))
        session.exec(delete(PolicyDecisionDB).where(PolicyDecisionDB.goal_id.in_(resolved_goal_ids)))
        session.exec(delete(MemoryEntryDB).where(MemoryEntryDB.goal_id.in_(resolved_goal_ids)))
        session.exec(delete(PlanDB).where(PlanDB.goal_id.in_(resolved_goal_ids)))
        session.exec(delete(GoalDB).where(GoalDB.id.in_(resolved_goal_ids)))
    session.commit()

    deleted["goal_ids"] = resolved_goal_ids
    deleted["task_ids"] = all_task_ids
    deleted["worker_job_ids"] = sorted(worker_job_ids)
    deleted["retrieval_run_ids"] = sorted(retrieval_run_ids)
    deleted["plan_ids"] = sorted(plan_ids)

print(json.dumps(deleted, ensure_ascii=True))
"""
    out = _docker_exec_hub_python(hub_container, script)
    return json.loads(out) if out else {}


def cleanup_test_environment(
    *,
    hub_base_url: str = DEFAULT_HUB_BASE_URL,
    hub_container: str = DEFAULT_HUB_CONTAINER,
    admin_user: str | None = None,
    admin_password: str | None = None,
    report_files: list[str] | None = None,
    cleanup_live_prefixes: bool = False,
    explicit_targets: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    report_targets = {"template_names": [], "blueprint_names": [], "team_names": [], "goal_ids": [], "team_ids": []}
    for raw_path in report_files or []:
        for key, values in _extract_targets_from_report(Path(raw_path)).items():
            report_targets[key].extend(values)
    explicit = explicit_targets or {}
    targets = {
        "template_names": _unique_strings(report_targets["template_names"] + list(explicit.get("template_names") or [])),
        "blueprint_names": _unique_strings(report_targets["blueprint_names"] + list(explicit.get("blueprint_names") or [])),
        "team_names": _unique_strings(report_targets["team_names"] + list(explicit.get("team_names") or [])),
        "goal_ids": _unique_strings(report_targets["goal_ids"] + list(explicit.get("goal_ids") or [])),
        "team_ids": _unique_strings(report_targets["team_ids"] + list(explicit.get("team_ids") or [])),
    }
    prefixes = DEFAULT_LIVE_PREFIXES if cleanup_live_prefixes else {"template_names": [], "blueprint_names": [], "team_names": []}

    user = str(admin_user or _env_value("E2E_ADMIN_USER") or _env_value("INITIAL_ADMIN_USER") or "").strip()
    password = str(admin_password or _env_value("E2E_ADMIN_PASSWORD") or _env_value("INITIAL_ADMIN_PASSWORD") or "").strip()
    if not user or not password:
        user, password = get_admin_from_hub_container(hub_container)
    token = login_token(hub_base_url, user, password)

    summary: dict[str, Any] = {
        "targets": targets,
        "cleanup_live_prefixes": cleanup_live_prefixes,
        "autopilot": {},
        "resolved": {},
        "tasks_cleanup": [],
        "goal_cleanup": {},
        "deleted": {"teams": [], "blueprints": [], "templates": []},
    }

    try:
        summary["autopilot"]["stop_before"] = req(hub_base_url, "POST", "/tasks/autopilot/stop", body={}, token=token, timeout=30)
    except Exception as exc:
        summary["autopilot"]["stop_before_error"] = str(exc)
    try:
        summary["autopilot"]["circuit_reset"] = req(
            hub_base_url, "POST", "/tasks/autopilot/circuits/reset", body={}, token=token, timeout=30
        )
    except Exception as exc:
        summary["autopilot"]["circuit_reset_error"] = str(exc)

    templates = _list_resources(hub_base_url, token, "/templates")
    blueprints = _list_resources(hub_base_url, token, "/teams/blueprints")
    teams = _list_resources(hub_base_url, token, "/teams")

    matched_templates = _match_resources(
        templates, exact_names=targets["template_names"], prefixes=prefixes["template_names"]
    )
    matched_blueprints = _match_resources(
        blueprints, exact_names=targets["blueprint_names"], prefixes=prefixes["blueprint_names"]
    )
    matched_teams = _match_resources(teams, exact_names=targets["team_names"], prefixes=prefixes["team_names"])

    team_ids = _unique_strings(targets["team_ids"] + [str(item.get("id") or "") for item in matched_teams])
    goal_ids = _unique_strings(targets["goal_ids"])

    summary["resolved"] = {
        "templates": [{"id": item.get("id"), "name": item.get("name")} for item in matched_templates],
        "blueprints": [{"id": item.get("id"), "name": item.get("name")} for item in matched_blueprints],
        "teams": [{"id": item.get("id"), "name": item.get("name")} for item in matched_teams],
        "team_ids": team_ids,
        "goal_ids": goal_ids,
    }

    for team_id in team_ids:
        try:
            active = req(
                hub_base_url,
                "POST",
                "/tasks/cleanup",
                body={"mode": "delete", "team_id": team_id},
                token=token,
                timeout=60,
            )
        except Exception as exc:
            active = {"error": str(exc)}
        try:
            archived = req(
                hub_base_url,
                "POST",
                "/tasks/archived/cleanup",
                body={"team_id": team_id},
                token=token,
                timeout=60,
            )
        except Exception as exc:
            archived = {"error": str(exc)}
        summary["tasks_cleanup"].append({"team_id": team_id, "active": active, "archived": archived})

    if goal_ids or team_ids:
        summary["goal_cleanup"] = _cleanup_goal_data_in_hub(hub_container, goal_ids, team_ids)

    for team in matched_teams:
        team_id = str(team.get("id") or "")
        if not team_id:
            continue
        try:
            req(hub_base_url, "DELETE", f"/teams/{team_id}", token=token, timeout=45)
            summary["deleted"]["teams"].append(team_id)
        except Exception as exc:
            summary.setdefault("delete_errors", []).append({"kind": "team", "id": team_id, "error": str(exc)})
    for blueprint in matched_blueprints:
        blueprint_id = str(blueprint.get("id") or "")
        if not blueprint_id:
            continue
        try:
            req(hub_base_url, "DELETE", f"/teams/blueprints/{blueprint_id}", token=token, timeout=45)
            summary["deleted"]["blueprints"].append(blueprint_id)
        except Exception as exc:
            summary.setdefault("delete_errors", []).append({"kind": "blueprint", "id": blueprint_id, "error": str(exc)})
    for template in matched_templates:
        template_id = str(template.get("id") or "")
        if not template_id:
            continue
        try:
            req(hub_base_url, "DELETE", f"/templates/{template_id}", token=token, timeout=45)
            summary["deleted"]["templates"].append(template_id)
        except Exception as exc:
            summary.setdefault("delete_errors", []).append({"kind": "template", "id": template_id, "error": str(exc)})

    try:
        summary["autopilot"]["stop_after"] = req(hub_base_url, "POST", "/tasks/autopilot/stop", body={}, token=token, timeout=30)
    except Exception as exc:
        summary["autopilot"]["stop_after_error"] = str(exc)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup test-created UI/live-click resources without touching Ollama volumes.")
    parser.add_argument("--hub-base-url", default=DEFAULT_HUB_BASE_URL)
    parser.add_argument("--hub-container", default=DEFAULT_HUB_CONTAINER)
    parser.add_argument("--admin-user", default=_env_value("E2E_ADMIN_USER", _env_value("INITIAL_ADMIN_USER", "admin")))
    parser.add_argument("--admin-password", default=_env_value("E2E_ADMIN_PASSWORD", _env_value("INITIAL_ADMIN_PASSWORD", "")))
    parser.add_argument("--report-file", action="append", default=[])
    parser.add_argument("--cleanup-live-prefixes", action="store_true")
    args = parser.parse_args()

    result = cleanup_test_environment(
        hub_base_url=args.hub_base_url,
        hub_container=args.hub_container,
        admin_user=args.admin_user,
        admin_password=args.admin_password,
        report_files=list(args.report_file or []),
        cleanup_live_prefixes=bool(args.cleanup_live_prefixes),
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
