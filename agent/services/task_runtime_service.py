from __future__ import annotations

import logging
import threading
import time
from typing import Any

from agent.common.gateways.worker_gateway import get_worker_gateway
from agent.db_models import TaskDB
from agent.repository import task_repo
from agent.services.hub_event_service import build_task_history_event
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_status_service import normalize_task_status
from agent.utils import _http_post

_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "skipped"}


def _maybe_finalize_goal(goal_id: str) -> None:
    try:
        from agent.repository import goal_repo

        goal_tasks = task_repo.get_by_goal_id(goal_id)
        if not goal_tasks:
            return
        statuses = {normalize_task_status(getattr(t, "status", None), default="todo") for t in goal_tasks}
        if not statuses.issubset(_TERMINAL_TASK_STATUSES):
            return
        goal = goal_repo.get_by_id(goal_id)
        if not goal or goal.status not in {"planned", "in_progress", "running"}:
            return
        new_status = "failed" if "failed" in statuses else "completed"
        goal.status = new_status
        goal.updated_at = time.time()
        goal_repo.save(goal)
        logging.info("Goal %s finalized as %s (all %d tasks terminal)", goal_id, new_status, len(goal_tasks))
    except Exception as exc:
        logging.warning("_maybe_finalize_goal(%s) error: %s", goal_id, exc)

_task_subscribers = []
_subscribers_lock = threading.Lock()


class TaskRuntimeService:
    def get_local_task_status(self, tid: str) -> dict[str, Any] | None:
        return get_local_task_status(tid)

    def notify_task_update(self, tid: str) -> None:
        notify_task_update(tid)

    def update_local_task_status(
        self,
        tid: str,
        status: str,
        event_type: str | None = None,
        event_actor: str = "system",
        event_details: dict | None = None,
        **kwargs,
    ) -> None:
        update_local_task_status(
            tid,
            status,
            event_type=event_type,
            event_actor=event_actor,
            event_details=event_details,
            **kwargs,
        )

    def forward_to_worker(self, worker_url: str, endpoint: str, data: dict, token: str | None = None) -> Any:
        return forward_to_worker(worker_url, endpoint, data, token=token)


def get_local_task_status(tid: str) -> dict[str, Any] | None:
    task = task_repo.get_by_id(tid)
    return task.model_dump() if task else None


def notify_task_update(tid: str) -> None:
    with _subscribers_lock:
        for subscriber_tid, queue in _task_subscribers:
            if subscriber_tid == tid or subscriber_tid == "*":
                queue.put(tid)


def append_task_history_event(task: TaskDB, event_type: str, actor: str = "system", details: dict | None = None) -> None:
    history = list(task.history or [])
    history.append(build_task_history_event(task, event_type, actor=actor, details=details, timestamp=time.time()))
    task.history = history[-200:]


def update_local_task_status(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    force: bool = False,
    **kwargs,
) -> None:
    # thr-004: thread-safe — each call opens its own SQLAlchemy Session via
    # `with Session(engine)` in the repository layer. The engine connection pool
    # (PostgreSQL QueuePool) is designed for concurrent multi-threaded access.
    # Concurrent calls for *different* task IDs are fully safe (row-level locking).
    # Concurrent calls for the *same* task ID are serialized by PostgreSQL row locks.
    task = task_repo.get_by_id(tid)
    if not task:
        task = TaskDB(id=tid, created_at=time.time(), status="todo")

    old_status = task.status
    normalized_status = normalize_task_status(status)

    if not force and old_status:
        ok, reason = can_transition_to(old_status, normalized_status)
        if not ok:
            logging.warning("Blockierter Statuswechsel fuer Task %s: %s (force=False)", tid, reason)
            # Wir blockieren hier aktiv, wenn es kein force-Request ist
            if old_status != normalized_status:
                 return

    task.status = normalized_status
    task.updated_at = time.time()

    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)

    if event_type:
        append_task_history_event(task, event_type=event_type, actor=event_actor, details=event_details or {})

    task_repo.save(task)
    notify_task_update(tid)

    if normalized_status in _TERMINAL_TASK_STATUSES and task.goal_id:
        _maybe_finalize_goal(task.goal_id)

    if task.callback_url:

        def send_callback() -> None:
            import agent.common.context

            if agent.common.context.shutdown_requested:
                return
            try:
                payload = {"id": tid, "status": normalized_status, "parent_task_id": task.parent_task_id}
                if task.last_output:
                    payload["last_output"] = task.last_output
                if task.last_exit_code is not None:
                    payload["last_exit_code"] = task.last_exit_code
                payload["worker_job_id"] = task.current_worker_job_id
                verification_status = dict(task.verification_status or {})
                execution_artifacts = verification_status.get("execution_artifacts")
                if isinstance(execution_artifacts, list):
                    payload["artifacts"] = execution_artifacts

                headers = {}
                if task.callback_token:
                    headers["Authorization"] = f"Bearer {task.callback_token}"

                _http_post(task.callback_url, data=payload, headers=headers)
                logging.info("Webhook an %s gesendet fuer Task %s", task.callback_url, tid)
            except Exception as exc:
                logging.error("Fehler beim Senden des Webhooks an %s: %s", task.callback_url, exc)

        thread = threading.Thread(target=send_callback, daemon=True)
        import agent.common.context

        agent.common.context.active_threads.append(thread)
        thread.start()


def forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str | None = None) -> Any:
    return get_worker_gateway().forward_task(worker_url, endpoint, data, token=token)


task_runtime_service = TaskRuntimeService()


def get_task_runtime_service() -> TaskRuntimeService:
    return task_runtime_service
