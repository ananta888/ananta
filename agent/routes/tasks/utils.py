from typing import Any

from agent.repository import task_repo
from agent.services.task_runtime_service import (
    _subscribers_lock,
    _task_subscribers,
    append_task_history_event,
    forward_to_worker,
    notify_task_update,
    update_local_task_status,
)

# Pub/Sub Mechanismus für Task-Updates (Liste von Tupeln: (tid, queue))
# In-Memory Cache für Tasks (Veraltet, durch Paginierung ersetzt)
_tasks_cache = None
_last_cache_update = 0
_last_archive_check = 0


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
    update_local_task_status(
        tid,
        status,
        event_type=event_type,
        event_actor=event_actor,
        event_details=event_details,
        **kwargs,
    )


def _forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str = None) -> Any:
    return forward_to_worker(worker_url, endpoint, data, token=token)
