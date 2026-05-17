import time
from typing import Any

from flask import current_app

from agent.db_models import TaskDB
from agent.config import settings
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import (
    _subscribers_lock,
    _task_subscribers,
    append_task_history_event,
    get_local_task_status,
    notify_task_update,
    update_local_task_status,
)
from agent.services.task_status_service import normalize_task_status
from agent.utils import _http_post


def _repos():
    return get_repository_registry()

# Pub/Sub Mechanismus für Task-Updates (Liste von Tupeln: (tid, queue))
# In-Memory Cache für Tasks (Veraltet, durch Paginierung ersetzt)
_tasks_cache = None
_last_cache_update = 0
_last_archive_check = 0
task_repo = get_repository_registry().task_repo


def _get_tasks_cache():
    # Diese Funktion wird nur noch intern verwendet, falls nötig.
    # Für öffentliche APIs wird jetzt Paginierung direkt im Repository genutzt.
    tasks = task_repo.get_all()
    return {t.id: t.model_dump() for t in tasks}


def _notify_task_update(tid: str):
    notify_task_update(tid)


def _get_local_task_status(tid: str):
    return get_local_task_status(tid)


def _update_local_task_status(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    **kwargs,
):
    update_local_task_status(
        tid,
        status,
        event_type=event_type,
        event_actor=event_actor,
        event_details=event_details,
        **kwargs,
    )


def _forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str = None) -> Any:
    timeout = int(getattr(settings, "http_timeout", 60) or 60)
    try:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if current_app else {}
    except RuntimeError:
        agent_cfg = {}
    command_timeout = max(1, int(agent_cfg.get("command_timeout") or timeout or 60))
    endpoint_name = str(endpoint or "").strip().lower()
    if endpoint_name.endswith("/step/propose"):
        timeout = max(timeout, command_timeout + 120, 180)
    else:
        timeout = max(timeout, command_timeout)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
    response = _http_post(url, data=data, headers=headers, timeout=timeout, return_response=True, silent=True)
    if response is None:
        return None
    # Preserve API envelope on success.
    if int(getattr(response, "status_code", 500) or 500) < 400:
        try:
            return response.json()
        except Exception:
            return {"status": "ok", "data": {}}
    # Structured error payload for caller-side diagnostics/backoff.
    code = int(getattr(response, "status_code", 500) or 500)
    body: Any
    try:
        body = response.json()
    except Exception:
        body = {"raw": str(getattr(response, "text", "") or "")[:600]}
    message = None
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        details = body.get("data") if isinstance(body.get("data"), dict) else body
    else:
        details = {"raw": str(body)}
    return {
        "status": "error",
        "message": str(message or f"http_{code}"),
        "http_status": code,
        "details": details,
        "worker_url": worker_url,
        "endpoint": endpoint,
    }
