import uuid
from typing import Any, Optional, Callable, Dict, List
import os
import json
import time
import logging
import portalocker
import threading
from queue import Queue, Empty
from flask import Blueprint, jsonify, current_app, request, g, Response
from agent.config import settings
from agent.utils import (
    validate_request, read_json, write_json, update_json, _extract_command, _extract_reason,
    _get_approved_command, _log_terminal_entry, rate_limit, _http_post, _archive_old_tasks
)
from agent.llm_integration import _call_llm
from agent.models import (
    TaskStepProposeRequest, TaskStepProposeResponse, 
    TaskStepExecuteRequest, TaskStepExecuteResponse
)
from agent.metrics import TASK_RECEIVED, TASK_COMPLETED, TASK_FAILED
from agent.shell import get_shell
from agent.auth import check_auth

tasks_bp = Blueprint("tasks", __name__)

# Pub/Sub Mechanismus für Task-Updates (Liste von Tupeln: (tid, queue))
_task_subscribers = []
_subscribers_lock = threading.Lock()

# In-Memory Cache für Tasks
_tasks_cache = None
_last_cache_update = 0
_last_archive_check = 0
_cache_lock = threading.Lock()

def _get_tasks_cache():
    global _tasks_cache, _last_cache_update
    path = current_app.config.get("TASKS_PATH", "data/tasks.json")
    
    with _cache_lock:
        # Prüfen, ob die Datei seit dem letzten Laden geändert wurde
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
            
        if _tasks_cache is None or mtime > _last_cache_update:
            _tasks_cache = read_json(path, {})
            _last_cache_update = mtime
        
        # Rückgabe einer Kopie, um externe Manipulation des Caches zu verhindern
        return _tasks_cache.copy() if _tasks_cache is not None else {}

def _notify_task_update(tid: str):
    with _subscribers_lock:
        for subscriber_tid, q in _task_subscribers:
            if subscriber_tid == tid or subscriber_tid == "*":
                q.put(tid)

def _get_local_task_status(tid: str):
    tasks = _get_tasks_cache()
    return tasks.get(tid)

def _update_local_task_status(tid: str, status: str, **kwargs):
    path = current_app.config["TASKS_PATH"]
    
    def update_tasks(tasks):
        if not isinstance(tasks, dict):
            tasks = {}
        if tid not in tasks:
            tasks[tid] = {"id": tid, "created_at": time.time()}
        tasks[tid].update({"status": status, "updated_at": time.time(), **kwargs})
        return tasks
    
    updated_tasks = update_json(path, update_tasks, default={})
    
    # Cache aktualisieren
    global _tasks_cache, _last_cache_update
    with _cache_lock:
        _tasks_cache = updated_tasks
        try:
            _last_cache_update = os.path.getmtime(path)
        except OSError:
            _last_cache_update = time.time()
    
    _notify_task_update(tid)

def _forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str = None) -> Any:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
    return _http_post(url, data=data, headers=headers)

@tasks_bp.route("/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def propose_step():
    data: TaskStepProposeRequest = g.validated_data
    cfg = current_app.config["AGENT_CONFIG"]
    
    provider = data.provider or cfg.get("provider", "ollama")
    model = data.model or cfg.get("model", "llama3")
    prompt = data.prompt or "Was soll ich als nächstes tun?"
    
    # Prompt an LLM senden
    raw_res = _call_llm(
        provider=provider,
        model=model,
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"]
    )
    
    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    
    # Persistenz falls task_id vorhanden
    if data.task_id:
        _update_local_task_status(data.task_id, "proposing", last_proposal={"reason": reason, "command": command})

    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=data.task_id)
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, task_id=data.task_id)
    
    return jsonify(TaskStepProposeResponse(
        reason=reason,
        command=command,
        raw=raw_res
    ).model_dump())

@tasks_bp.route("/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def execute_step():
    data: TaskStepExecuteRequest = g.validated_data
    shell = get_shell()
    output, exit_code = shell.execute(data.command, timeout=data.timeout or 60)
    
    # Persistenz falls task_id vorhanden
    if data.task_id:
        status = "completed" if exit_code == 0 else "failed"
        _update_local_task_status(data.task_id, status, last_output=output, last_exit_code=exit_code)
        if status == "completed": TASK_COMPLETED.inc()
        else: TASK_FAILED.inc()

    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", command=data.command, task_id=data.task_id)
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", output=output, exit_code=exit_code, task_id=data.task_id)
    
    return jsonify(TaskStepExecuteResponse(
        output=output,
        exit_code=exit_code,
        task_id=data.task_id
    ).model_dump())

@tasks_bp.route("/logs", methods=["GET"])
@check_auth
def get_logs():
    log_file = os.path.join(current_app.config["DATA_DIR"], "terminal_log.jsonl")
    if not os.path.exists(log_file):
        return jsonify([])
    
    logs = []
    try:
        with portalocker.Lock(log_file, mode="r", encoding="utf-8", timeout=5, flags=portalocker.LOCK_SH) as f:
            for line in f:
                try:
                    logs.append(json.loads(line))
                except Exception as e:
                    logging.debug(f"Ignoriere ungültige Log-Zeile: {e}")
    except Exception as e:
        logging.error(f"Fehler beim Lesen der Logs: {e}")
        return jsonify({"error": "could_not_read_logs"}), 500

    return jsonify(logs[-100:]) # Letzte 100 Einträge

@tasks_bp.route("/tasks", methods=["GET"])
@check_auth
def list_tasks():
    """
    Alle Tasks auflisten
    ---
    security:
      - Bearer: []
    parameters:
      - name: status
        in: query
        type: string
        description: Filter nach Status
      - name: agent
        in: query
        type: string
        description: Filter nach Agent URL
      - name: since
        in: query
        type: number
        description: Filter nach Erstellungszeitpunkt (ab)
      - name: until
        in: query
        type: number
        description: Filter nach Erstellungszeitpunkt (bis)
    responses:
      200:
        description: Liste aller Tasks
    """
    status_filter = request.args.get("status")
    agent_filter = request.args.get("agent")
    since_filter = request.args.get("since", type=float)
    until_filter = request.args.get("until", type=float)

    tasks = _get_tasks_cache()
    task_list = list(tasks.values())

    if status_filter:
        task_list = [t for t in task_list if t.get("status") == status_filter]
    
    if agent_filter:
        task_list = [t for t in task_list if t.get("assigned_agent_url") == agent_filter]
        
    if since_filter:
        task_list = [t for t in task_list if t.get("created_at", 0) >= since_filter]
        
    if until_filter:
        task_list = [t for t in task_list if t.get("created_at", 0) <= until_filter]

    return jsonify(task_list)

@tasks_bp.route("/tasks", methods=["POST"])
@check_auth
def create_task():
    """
    Neuen Task erstellen
    ---
    security:
      - Bearer: []
    responses:
      201:
        description: Task erstellt
    """
    data = request.get_json()
    tid = data.get("id") or str(uuid.uuid4())
    _update_local_task_status(tid, "created", **data)
    TASK_RECEIVED.inc()
    return jsonify({"id": tid, "status": "created"}), 201

@tasks_bp.route("/tasks/<tid>", methods=["GET"])
@check_auth
def get_task(tid):
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    return jsonify(task)

@tasks_bp.route("/tasks/<tid>", methods=["PATCH"])
@check_auth
def patch_task(tid):
    data = request.get_json()
    _update_local_task_status(tid, data.get("status", "updated"), **data)
    return jsonify({"id": tid, "status": "updated"})

@tasks_bp.route("/tasks/<tid>/assign", methods=["POST"])
@check_auth
def assign_task(tid):
    data = request.get_json()
    agent_url = data.get("agent_url")
    agent_token = data.get("token")
    
    if not agent_url:
        return jsonify({"error": "agent_url_required"}), 400
        
    _update_local_task_status(tid, "assigned", assigned_agent_url=agent_url, assigned_agent_token=agent_token)
    return jsonify({"status": "assigned", "agent_url": agent_url})

@tasks_bp.route("/tasks/<tid>/unassign", methods=["POST"])
@check_auth
def unassign_task(tid):
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
        
    _update_local_task_status(tid, "todo", assigned_agent_url=None, assigned_agent_token=None, assigned_to=None)
    return jsonify({"status": "todo", "unassigned": True})

@tasks_bp.route("/tasks/<tid>/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def task_propose(tid):
    data: TaskStepProposeRequest = g.validated_data
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    
    # Weiterleitung an Worker falls zugewiesen
    worker_url = task.get("assigned_agent_url")
    if worker_url:
        # Nur weiterleiten wenn es nicht wir selbst sind
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") != my_url.rstrip("/"):
            try:
                res = _forward_to_worker(
                    worker_url, 
                    f"/tasks/{tid}/step/propose", 
                    data.model_dump(), 
                    token=task.get("assigned_agent_token")
                )
                # Lokalen Status synchronisieren
                if isinstance(res, dict) and "command" in res:
                     _update_local_task_status(tid, "proposing", last_proposal=res)
                return jsonify(res)
            except Exception as e:
                logging.error(f"Forwarding an {worker_url} fehlgeschlagen: {e}")
                return jsonify({"error": "forwarding_failed", "message": str(e)}), 502

    cfg = current_app.config["AGENT_CONFIG"]
    base_prompt = data.prompt or task.get("description") or task.get("prompt") or "Bearbeite Task " + tid
    
    prompt = (
        f"{base_prompt}\n\n"
        "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
        "{\n"
        "  \"reason\": \"Kurze Begründung\",\n"
        "  \"command\": \"Shell-Befehl\"\n"
        "}"
    )
    
    raw_res = _call_llm(
        provider=cfg.get("provider", "ollama"),
        model=cfg.get("model", "llama3"),
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"],
        history=task.get("history", [])
    )
    
    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    
    _update_local_task_status(tid, "proposing", last_proposal={"reason": reason, "command": command})
    
    res = TaskStepProposeResponse(reason=reason, command=command, raw=raw_res)
    return jsonify(res.model_dump())

@tasks_bp.route("/tasks/<tid>/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def task_execute(tid):
    data: TaskStepExecuteRequest = g.validated_data
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    
    # Weiterleitung an Worker falls zugewiesen
    worker_url = task.get("assigned_agent_url")
    if worker_url:
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") != my_url.rstrip("/"):
            try:
                res = _forward_to_worker(
                    worker_url, 
                    f"/tasks/{tid}/step/execute", 
                    data.model_dump(), 
                    token=task.get("assigned_agent_token")
                )
                
                # Lokalen Status und Historie im Hub synchronisieren
                if isinstance(res, dict) and "status" in res:
                    history = task.get("history", [])
                    history.append({
                        "prompt": task.get("description"),
                        "reason": "Forwarded to " + worker_url,
                        "command": data.command or task.get("last_proposal", {}).get("command"),
                        "output": res.get("output"),
                        "exit_code": res.get("exit_code"),
                        "timestamp": time.time()
                    })
                    _update_local_task_status(tid, res["status"], history=history)
                
                return jsonify(res)
            except Exception as e:
                logging.error(f"Forwarding (Execute) an {worker_url} fehlgeschlagen: {e}")
                return jsonify({"error": "forwarding_failed", "message": str(e)}), 502

    command = data.command
    reason = "Direkte Ausführung"
    
    if not command:
        proposal = task.get("last_proposal")
        if not proposal:
            return jsonify({"error": "no_proposal"}), 400
        command = proposal.get("command")
        reason = proposal.get("reason", "Vorschlag ausgeführt")
    
    shell = get_shell()
    output, exit_code = shell.execute(command, timeout=data.timeout or 60)
    
    history = task.get("history", [])
    history.append({
        "prompt": task.get("description"),
        "reason": reason,
        "command": command,
        "output": output,
        "exit_code": exit_code,
        "timestamp": time.time()
    })
    
    status = "completed" if exit_code == 0 else "failed"
    if status == "completed": TASK_COMPLETED.inc()
    else: TASK_FAILED.inc()

    _update_local_task_status(tid, status, history=history)

    _log_terminal_entry(current_app.config["AGENT_NAME"], len(history), "out", command=command, task_id=tid)
    _log_terminal_entry(current_app.config["AGENT_NAME"], len(history), "in", output=output, exit_code=exit_code, task_id=tid)

    res = TaskStepExecuteResponse(output=output, exit_code=exit_code, task_id=tid, status=status)
    return jsonify(res.model_dump())

@tasks_bp.route("/tasks/<tid>/logs", methods=["GET"])
@check_auth
def task_logs(tid):
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    return jsonify(task.get("history", []))

@tasks_bp.route("/tasks/<tid>/stream-logs", methods=["GET"])
@check_auth
def stream_task_logs(tid):
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
                
                # Warten auf Benachrichtigung oder Timeout für Keep-Alive
                try:
                    # Wir warten bis zu 15 Sekunden auf ein Update für DIESE Task-ID
                    q.get(timeout=15)
                except Empty:
                    yield ": keep-alive\n\n"
        finally:
            with _subscribers_lock:
                # Sicherstellen, dass wir genau unser Tupel entfernen
                for i, (t_id, t_q) in enumerate(_task_subscribers):
                    if t_id == tid and t_q is q:
                        _task_subscribers.pop(i)
                        break
                
    return Response(generate(), mimetype="text/event-stream")
