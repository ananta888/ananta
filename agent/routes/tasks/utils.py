from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import (
    _subscribers_lock as _subscribers_lock,
)
from agent.services.task_runtime_service import (
    _task_subscribers as _task_subscribers,
)
from agent.services.task_runtime_service import (
    get_local_task_status,
    notify_task_update,
)


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


# --- Re-exports (CLN-048): implementation moved to agent.services.task_forwarding_utils
from agent.services.task_forwarding_utils import (  # noqa: E402,F401
    _deadline_bounded_chunks,
    _forward_to_worker,
    _parse_bounded_knowledge_index_worker_response,
    _parse_bounded_recovery_worker_response,
    _parse_bounded_vector_worker_response,
    _parse_bounded_worker_response,
    _step_deadline_monotonic,
    _step_forward_deadline_seconds,
    _update_local_task_status,
    _worker_error_payload,
)
