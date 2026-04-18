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
