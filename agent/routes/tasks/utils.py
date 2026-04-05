import time
from typing import Any

from flask import current_app

from agent.db_models import TaskDB
from agent.config import settings
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import _subscribers_lock, _task_subscribers, append_task_history_event, notify_task_update
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
    task = task_repo.get_by_id(tid)
    return task.model_dump() if task else None


def _update_local_task_status(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    **kwargs,
):
    task = task_repo.get_by_id(tid)
    if not task:
        task = TaskDB(id=tid, created_at=time.time())

    task.status = normalize_task_status(status)
    task.updated_at = time.time()

    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)

    if event_type:
        append_task_history_event(task, event_type=event_type, actor=event_actor, details=event_details or {})

    task_repo.save(task)
    _notify_task_update(tid)


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
    return _http_post(f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}", data=data, headers=headers, timeout=timeout)
