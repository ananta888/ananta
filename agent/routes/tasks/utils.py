import threading
import time
import logging
from typing import Any, Optional
from flask import Blueprint, jsonify, request
from agent.repository import task_repo, scheduled_task_repo
from agent.db_models import TaskDB
from agent.utils import _http_post

# Pub/Sub Mechanismus für Task-Updates (Liste von Tupeln: (tid, queue))
_task_subscribers = []
_subscribers_lock = threading.Lock()

# In-Memory Cache für Tasks
_tasks_cache = None
_last_cache_update = 0
_last_archive_check = 0
_cache_lock = threading.Lock()

def _get_tasks_cache():
    tasks = task_repo.get_all()
    return {t.id: t.model_dump() for t in tasks}

def _notify_task_update(tid: str):
    with _subscribers_lock:
        for subscriber_tid, q in _task_subscribers:
            if subscriber_tid == tid or subscriber_tid == "*":
                q.put(tid)

def _get_local_task_status(tid: str):
    task = task_repo.get_by_id(tid)
    return task.model_dump() if task else None

def _update_local_task_status(tid: str, status: str, **kwargs):
    task = task_repo.get_by_id(tid)
    if not task:
        task = TaskDB(id=tid, created_at=time.time())
    
    task.status = status
    task.updated_at = time.time()
    
    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)
    
    task_repo.save(task)
    _notify_task_update(tid)

    # Webhook-Callback falls konfiguriert
    if task.callback_url:
        def send_callback():
            try:
                payload = {
                    "id": tid,
                    "status": status,
                    "parent_task_id": task.parent_task_id
                }
                # Füge weitere nützliche Infos hinzu
                if task.last_output:
                    payload["last_output"] = task.last_output
                if task.last_exit_code is not None:
                    payload["last_exit_code"] = task.last_exit_code
                
                headers = {}
                if task.callback_token:
                    headers["Authorization"] = f"Bearer {task.callback_token}"
                
                _http_post(task.callback_url, data=payload, headers=headers)
                logging.info(f"Webhook an {task.callback_url} gesendet für Task {tid}")
            except Exception as e:
                logging.error(f"Fehler beim Senden des Webhooks an {task.callback_url}: {e}")

        threading.Thread(target=send_callback, daemon=True).start()

def _forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str = None) -> Any:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
    return _http_post(url, data=data, headers=headers)
