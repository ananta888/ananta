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
from pathlib import Path
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
    if section_id == "templates":
        return _fetch_templates(base, jwt, t)
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


def _fetch_templates(base: str, token: str, timeout: float) -> SectionLoadResult:
    per = max(0.5, timeout / 2)

    blueprints_raw: list[dict] = []
    try:
        data = _checked_get(base, "/teams/blueprints", token, per)
        blueprints_raw = data if isinstance(data, list) else []
    except Exception:
        pass

    templates_raw: list[dict] = []
    try:
        data = _checked_get(base, "/templates", token, per)
        templates_raw = data if isinstance(data, list) else []
    except Exception:
        pass

    fallback_used = False
    if not blueprints_raw:
        fallback_blueprints = _load_local_blueprint_fallback()
        if fallback_blueprints:
            blueprints_raw = fallback_blueprints
            fallback_used = True
    if not templates_raw:
        fallback_templates = _load_local_templates_fallback()
        if fallback_templates:
            templates_raw = fallback_templates
            fallback_used = True

    blueprint_items = [
        {
            "id": f"bp:{str(b.get('id') or '')}",
            "kind": "blueprint",
            "title": str(b.get("name") or b.get("id") or "")[:60],
            "description": str(b.get("description") or "")[:100],
            "roles_count": int(b.get("roles_count") or len(b.get("roles") or [])),
            "artifacts_count": int(b.get("artifacts_count") or len(b.get("artifacts") or [])),
            "is_seed": bool(b.get("is_seed", False)),
            "base_team_type": str(b.get("base_team_type_name") or ""),
            "raw_id": str(b.get("id") or ""),
        }
        for b in blueprints_raw
    ]

    role_template_items = []
    system_prompt_items = []
    for t in templates_raw:
        name = str(t.get("name") or t.get("id") or "")
        is_system = name.startswith("system.")
        entry = {
            "id": f"tpl:{str(t.get('id') or '')}",
            "kind": "system_prompt" if is_system else "template",
            "title": name[:60],
            "description": str(t.get("description") or "")[:100],
            "prompt_preview": str(t.get("prompt_template") or "")[:120].replace("\n", " "),
            "service": str(t.get("service") or ""),
            "is_seed": bool(t.get("is_seed", False)),
            "raw_id": str(t.get("id") or ""),
        }
        if is_system:
            system_prompt_items.append(entry)
        else:
            role_template_items.append(entry)

    items = blueprint_items + role_template_items + system_prompt_items
    payload: dict[str, Any] = {
        "items": items,
        "blueprints_count": len(blueprint_items),
        "templates_count": len(role_template_items),
        "system_prompts_count": len(system_prompt_items),
        "blueprints_raw": blueprints_raw,
        "templates_raw": templates_raw,
    }
    panel_state = PanelState.HEALTHY if items else PanelState.EMPTY
    source = "hub+local" if fallback_used else "hub"
    return SectionLoadResult(
        "templates", panel_state, payload,
        f"{source}: {len(blueprint_items)} blueprints · {len(role_template_items)} templates · {len(system_prompt_items)} system",
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_local_blueprint_fallback() -> list[dict[str, Any]]:
    data = _read_json_file(_repo_root() / "config" / "blueprints" / "standard" / "templates.json")
    team_types = data.get("team_types") if isinstance(data, dict) else {}
    if not isinstance(team_types, dict):
        return []
    items: list[dict[str, Any]] = []
    for team_type, spec in team_types.items():
        if not isinstance(spec, dict):
            continue
        roles = spec.get("roles")
        items.append(
            {
                "id": f"seed-bp:{team_type}",
                "name": str(team_type),
                "description": str(spec.get("description") or ""),
                "roles_count": len(roles) if isinstance(roles, list) else 0,
                "artifacts_count": 0,
                "is_seed": True,
                "base_team_type_name": str(team_type),
            }
        )
    return items


def _load_local_templates_fallback() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seed_data = _read_json_file(_repo_root() / "config" / "blueprints" / "standard" / "templates.json")
    seed_templates = seed_data.get("templates") if isinstance(seed_data, dict) else []
    if isinstance(seed_templates, list):
        for idx, raw in enumerate(seed_templates):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or f"seed.template.{idx}")
            out.append(
                {
                    "id": f"seed-tpl:{name}",
                    "name": name,
                    "description": str(raw.get("description") or ""),
                    "prompt_template": str(raw.get("prompt_template") or ""),
                    "service": "",
                    "is_seed": True,
                }
            )
    sys_data = _read_json_file(_repo_root() / "config" / "system_prompts.json")
    prompts = sys_data.get("prompts") if isinstance(sys_data, dict) else []
    if isinstance(prompts, list):
        for idx, raw in enumerate(prompts):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or f"system.prompt.{idx}")
            out.append(
                {
                    "id": f"seed-sys:{name}",
                    "name": name,
                    "description": str(raw.get("description") or raw.get("label") or ""),
                    "prompt_template": str(raw.get("prompt_template") or ""),
                    "service": str(raw.get("service") or ""),
                    "is_seed": True,
                }
            )
    return out
