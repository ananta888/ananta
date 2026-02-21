import json
import logging
import os
from queue import Empty, Queue

import portalocker
from flask import Blueprint, Response, current_app

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.tasks.utils import _get_local_task_status, _subscribers_lock, _task_subscribers

logging_bp = Blueprint("tasks_logging", __name__)


@logging_bp.route("/logs", methods=["GET"])
@check_auth
def get_logs():
    """
    Globale Terminal-Logs abrufen
    ---
    responses:
      200:
        description: Liste der letzten 100 Log-Einträge
    """
    log_file = os.path.join(current_app.config["DATA_DIR"], "terminal_log.jsonl")
    if not os.path.exists(log_file):
        return api_response(data=[])

    logs = []
    try:
        with portalocker.Lock(
            log_file, mode="r", encoding="utf-8", timeout=5, flags=portalocker.LOCK_SH | portalocker.LOCK_NB
        ) as f:
            for line in f:
                try:
                    logs.append(json.loads(line))
                except Exception as e:
                    logging.debug(f"Ignoriere ungültige Log-Zeile: {e}")
    except Exception as e:
        logging.error(f"Fehler beim Lesen der Logs: {e}")
        return api_response(status="error", message="could_not_read_logs", code=500)

    return api_response(data=logs[-100:])


@logging_bp.route("/tasks/<tid>/logs", methods=["GET"])
@check_auth
def task_logs(tid):
    """
    Task-spezifische Historie abrufen
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Historie des Tasks
    """
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=task.get("history", []))


@logging_bp.route("/tasks/<tid>/stream-logs", methods=["GET"])
@check_auth
def stream_task_logs(tid):
    """
    Echtzeit-Logs eines Tasks (SSE)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Log-Stream
    """

    def generate():
        q = Queue()
        with _subscribers_lock:
            _task_subscribers.append((tid, q))

        try:
            last_idx = 0
            while True:
                task = _get_local_task_status(tid)
                if not task:
                    break

                history = task.get("history", [])
                if len(history) > last_idx:
                    for i in range(last_idx, len(history)):
                        yield f"data: {json.dumps(history[i])}\n\n"
                    last_idx = len(history)

                if task.get("status") in ("completed", "failed"):
                    break

                try:
                    q.get(timeout=15)
                except Empty:
                    yield ": keep-alive\n\n"
        finally:
            with _subscribers_lock:
                for i, (t_id, t_q) in enumerate(_task_subscribers):
                    if t_id == tid and t_q is q:
                        _task_subscribers.pop(i)
                        break

    return Response(generate(), mimetype="text/event-stream")
