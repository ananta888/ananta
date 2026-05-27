"""Loads TUI section payloads from the live Ananta hub API.

Uses stdlib urllib only — no extra deps.  Synchronous with per-call timeout.
Returns None for sections that have no hub backend (caller uses fixture).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from client_surfaces.operator_tui.models import PanelState, SectionLoadResult


def _hub_get(base: str, path: str, token: str, timeout: float) -> Any:
    url = f"{base}/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read())
    return raw.get("data")


def _goal_title(g: dict) -> str:
    return str(g.get("summary") or g.get("goal") or g.get("id") or "")[:80]


def _normalize_goal_status(status: str) -> str:
    s = str(status or "").lower()
    if s in {"done", "completed"}:
        return "done"
    if s in {"failed"}:
        return "failed"
    if s in {"draft", "blocked"}:
        return "blocked"
    return "running"


def _normalize_task_status(status: str) -> str:
    s = str(status or "").lower()
    if s in {"completed", "done"}:
        return "done"
    if s == "failed":
        return "failed"
    if s in {"cancelled", "canceled"}:
        return "blocked"
    return "running"


def _agent_label(task: dict) -> str:
    url = str(task.get("assigned_agent_url") or "")
    if not url:
        return "agent"
    parts = url.rstrip("/").split("/")
    last = parts[-1] if parts else ""
    return last or url.split("//")[-1].split(":")[0] or "agent"


def fetch_hub_section(
    section_id: str,
    endpoint: str,
    token: str,
    timeout: float = 2.0,
) -> SectionLoadResult | None:
    """Fetch live data for one TUI section.

    Returns None when the section has no hub backend (caller falls back to fixture).
    Raises PermissionError on HTTP 401/403.
    Raises TimeoutError / OSError on network failures.
    """
    base = endpoint.rstrip("/")
    t = min(max(0.5, float(timeout)), 5.0)

    if section_id == "goals":
        return _fetch_goals(base, token, t)
    if section_id == "tasks":
        return _fetch_tasks(base, token, t)
    if section_id == "artifacts":
        return _fetch_artifacts(base, token, t)
    if section_id == "dashboard":
        return _fetch_dashboard(base, token, t)
    if section_id == "system":
        return _fetch_system(base, token, t)
    return None


# ── per-section fetchers ───────────────────────────────────────────────────────

def _checked_get(base: str, path: str, token: str, timeout: float) -> Any:
    """Wraps _hub_get, converts HTTP 401/403 to PermissionError."""
    try:
        return _hub_get(base, path, token, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError(f"hub returned HTTP {exc.code} for {path}")
        raise


def _fetch_goals(base: str, token: str, timeout: float) -> SectionLoadResult:
    data = _checked_get(base, "/goals", token, timeout)
    goals: list[dict] = data if isinstance(data, list) else []
    items = [
        {
            "id": str(g.get("id") or ""),
            "status": _normalize_goal_status(str(g.get("status") or "")),
            "title": _goal_title(g),
        }
        for g in goals
    ]
    running = sum(1 for i in items if i["status"] == "running")
    done = sum(1 for i in items if i["status"] == "done")
    failed = sum(1 for i in items if i["status"] == "failed")
    payload: dict[str, Any] = {
        "items": items,
        "goal_summary": f"{running} running · {done} done · {failed} failed",
    }
    state = PanelState.HEALTHY if items else PanelState.EMPTY
    return SectionLoadResult("goals", state, payload, f"hub: {len(items)} goals")


def _fetch_tasks(base: str, token: str, timeout: float) -> SectionLoadResult:
    per = max(0.5, timeout / 2)
    data = _checked_get(base, "/tasks?limit=50", token, per)
    tasks: list[dict] = data if isinstance(data, list) else []
    items = [
        {
            "id": str(t.get("id") or ""),
            "status": _normalize_task_status(str(t.get("status") or "")),
            "agent": _agent_label(t),
            "title": str(t.get("title") or t.get("id") or "")[:80],
        }
        for t in tasks
    ]
    timeline: list[dict] = []
    try:
        tl_data = _checked_get(base, "/tasks/timeline?limit=10", token, per)
        tl_items = (tl_data or {}).get("items") if isinstance(tl_data, dict) else []
        timeline = [
            {
                "id": str(e.get("task_id") or e.get("id") or ""),
                "summary": str(e.get("summary") or e.get("event_type") or "")[:80],
            }
            for e in (tl_items or [])
        ]
    except Exception:
        pass
    payload: dict[str, Any] = {"items": items, "timeline": timeline}
    state = PanelState.HEALTHY if items else PanelState.EMPTY
    return SectionLoadResult("tasks", state, payload, f"hub: {len(items)} tasks")


def _fetch_artifacts(base: str, token: str, timeout: float) -> SectionLoadResult:
    data = _checked_get(base, "/artifacts", token, timeout)
    artifacts: list[dict] = data if isinstance(data, list) else []
    items = [
        {
            "id": str(a.get("id") or ""),
            "status": str(a.get("status") or "stored"),
            "title": str(a.get("latest_filename") or a.get("id") or "")[:80],
        }
        for a in artifacts
    ]
    payload: dict[str, Any] = {"items": items}
    state = PanelState.HEALTHY if items else PanelState.EMPTY
    return SectionLoadResult("artifacts", state, payload, f"hub: {len(items)} artifacts")


def _fetch_dashboard(base: str, token: str, timeout: float) -> SectionLoadResult:
    per = max(0.5, timeout / 3)
    health: dict = _checked_get(base, "/health", token, per) or {}
    checks = health.get("checks") or {}
    llm_providers: dict = checks.get("llm_providers") or {}
    queue_info: dict = checks.get("queue") or {}

    goal_summary = ""
    task_summary = ""
    agents_info: dict = {}
    try:
        goals_data = _checked_get(base, "/goals", token, per)
        goals: list = goals_data if isinstance(goals_data, list) else []
        running = sum(1 for g in goals if _normalize_goal_status(str(g.get("status") or "")) == "running")
        done = sum(1 for g in goals if _normalize_goal_status(str(g.get("status") or "")) == "done")
        failed = sum(1 for g in goals if _normalize_goal_status(str(g.get("status") or "")) == "failed")
        goal_summary = f"{running} running · {done} done · {failed} failed"
    except Exception:
        pass
    try:
        tasks_data = _checked_get(base, "/tasks?limit=100", token, per)
        tasks: list = tasks_data if isinstance(tasks_data, list) else []
        active = sum(1 for t in tasks if _normalize_task_status(str(t.get("status") or "")) == "running")
        completed = sum(1 for t in tasks if _normalize_task_status(str(t.get("status") or "")) == "done")
        task_summary = f"{active} active · {completed} completed"
        agents_info = {"online": 1, "total": 1}
    except Exception:
        pass

    payload: dict[str, Any] = {
        "agents": agents_info,
        "llm_providers": llm_providers,
        "queue": {"depth": queue_info.get("depth", 0)},
        "goal_summary": goal_summary,
        "task_summary": task_summary,
    }
    return SectionLoadResult("dashboard", PanelState.HEALTHY, payload, "hub: dashboard")


def _fetch_system(base: str, token: str, timeout: float) -> SectionLoadResult:
    per = max(0.5, timeout / 2)
    health: dict = _checked_get(base, "/health", token, per) or {}
    checks = health.get("checks") or {}
    llm_providers: dict = checks.get("llm_providers") or {}
    queue_info: dict = checks.get("queue") or {}
    queue_counts: dict = queue_info.get("counts") or {}

    contracts: list[str] = []
    try:
        contracts_data = _checked_get(base, "/contracts", token, per)
        if isinstance(contracts_data, list):
            contracts = [str(c) for c in contracts_data]
    except Exception:
        pass

    payload: dict[str, Any] = {
        "agents": {"online": 1, "total": 1},
        "llm_providers": llm_providers,
        "queue": {"depth": queue_info.get("depth", 0), "counts": queue_counts},
        "contracts": contracts,
    }
    return SectionLoadResult("system", PanelState.HEALTHY, payload, "hub: system")
