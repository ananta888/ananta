"""Loads TUI section payloads from the live Ananta hub API.

Uses stdlib urllib only — no extra deps.  Synchronous with per-call timeout.
Returns None for sections that have no hub backend (caller uses empty state).

Auth: if the caller passes a plain password (ANANTA_PASSWORD) rather than a JWT
(ANANTA_AUTH_TOKEN), login() is called automatically and the resulting JWT is
cached per (base, username) for its full TTL.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from client_surfaces.operator_tui.models import PanelState, SectionLoadResult

# ── JWT cache: (base_url, username) → (jwt_str, expires_at) ──────────────────
_jwt_cache: dict[tuple[str, str], tuple[str, float]] = {}
_jwt_lock = threading.Lock()


def _login(base: str, username: str, password: str, timeout: float = 3.0) -> str:
    """POST /login, return the JWT access token.  Raises on failure."""
    url = f"{base}/login"
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read())
    data = raw.get("data") or {}
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise ValueError(f"login response missing access_token: {raw}")
    # Decode exp from JWT payload (base64 middle segment) without a crypto dep
    try:
        import base64
        parts = token.split(".")
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(padded)).get("exp", 0)
        expires_at = float(exp) - 30  # 30s safety margin
    except Exception:
        expires_at = time.time() + 3600 - 30
    return token, expires_at


def resolve_token(base: str, raw_token: str) -> str:
    """Return a valid JWT.

    If raw_token looks like a JWT (contains dots), use it directly.
    Otherwise treat it as a password and login to get a JWT.
    Username comes from ANANTA_USER env var, defaulting to 'admin'.
    """
    if not raw_token:
        return ""
    # JWTs have exactly 2 dots (header.payload.sig)
    if raw_token.count(".") >= 2:
        return raw_token
    username = str(os.environ.get("ANANTA_USER") or "admin").strip()
    cache_key = (base, username)
    with _jwt_lock:
        cached = _jwt_cache.get(cache_key)
        if cached and time.time() < cached[1]:
            return cached[0]
    jwt_str, exp = _login(base, username, raw_token, timeout=3.0)
    with _jwt_lock:
        _jwt_cache[cache_key] = (jwt_str, exp)
    return jwt_str


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

    Returns None when the section has no hub backend (caller returns EMPTY state).
    Raises PermissionError on HTTP 401/403.
    Raises TimeoutError / OSError on network failures.
    """
    base = endpoint.rstrip("/")
    t = min(max(0.5, float(timeout)), 5.0)
    jwt = resolve_token(base, token)

    if section_id == "goals":
        return _fetch_goals(base, jwt, t)
    if section_id == "tasks":
        return _fetch_tasks(base, jwt, t)
    if section_id == "artifacts":
        return _fetch_artifacts(base, jwt, t)
    if section_id == "dashboard":
        return _fetch_dashboard(base, jwt, t)
    if section_id == "system":
        return _fetch_system(base, jwt, t)
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
    agents_check: dict = checks.get("agents") or {}
    agents_info: dict = {
        "online": agents_check.get("online", 0),
        "total": agents_check.get("total", 0),
    }

    goal_summary = ""
    task_summary = ""
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

    agents_check: dict = checks.get("agents") or {}
    payload: dict[str, Any] = {
        "agents": {
            "online": agents_check.get("online", 0),
            "total": agents_check.get("total", 0),
        },
        "llm_providers": llm_providers,
        "queue": {"depth": queue_info.get("depth", 0), "counts": queue_counts},
        "contracts": contracts,
    }
    return SectionLoadResult("system", PanelState.HEALTHY, payload, "hub: system")
