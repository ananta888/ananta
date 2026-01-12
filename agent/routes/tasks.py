import os
import json
import time
import logging
import portalocker
import threading
from queue import Queue, Empty
from flask import Blueprint, jsonify, current_app, request, g, Response
from agent.utils import (
    validate_request, read_json, write_json, update_json, _extract_command, _extract_reason,
    _get_approved_command, _log_terminal_entry, rate_limit
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

# Pub/Sub Mechanismus für Task-Updates
_task_subscribers = []
_subscribers_lock = threading.Lock()

# In-Memory Cache für Tasks
_tasks_cache = None
_last_cache_update = 0
_last_archive_check = 0
_cache_lock = threading.Lock()

def _archive_old_tasks(tasks_path=None):
    if tasks_path is None:
        try:
            tasks_path = current_app.config.get("TASKS_PATH", "data/tasks.json")
        except RuntimeError:
            # Falls außerhalb des App-Kontexts aufgerufen
            from agent.config import settings
            tasks_path = os.path.join(settings.data_dir, "tasks.json")

    archive_path = tasks_path.replace(".json", "_archive.json")
    
    from agent.config import settings
    retention_days = settings.tasks_retention_days
    
    now = time.time()
    cutoff = now - (retention_days * 86400)
    
    def update_func(tasks):
        if not isinstance(tasks, dict): return tasks
        to_archive = {}
        remaining = {}
        for tid, task in tasks.items():
            created_at = task.get("created_at", now)
            if created_at < cutoff:
                to_archive[tid] = task
            else:
                remaining[tid] = task
        
        if to_archive:
            logging.info(f"Archiviere {len(to_archive)} Tasks in {archive_path}")
            def update_archive(archive_data):
                if not isinstance(archive_data, dict): archive_data = {}
                archive_data.update(to_archive)
                return archive_data
            
            update_json(archive_path, update_archive, default={})
            return remaining
        return tasks

    update_json(tasks_path, update_func, default={})

def _get_tasks_cache():
    global _tasks_cache, _last_cache_update, _last_archive_check
    path = current_app.config.get("TASKS_PATH", "data/tasks.json")
    
    # Archivierung prüfen (max. einmal pro Stunde)
    now = time.time()
    if now - _last_archive_check > 3600:
        with _cache_lock:
            # Doppelte Prüfung innerhalb des Locks
            if now - _last_archive_check > 3600:
                _last_archive_check = now
                # Asynchron ausführen, um API-Antwortzeit nicht zu beeinträchtigen
                threading.Thread(target=_archive_old_tasks, args=(path,), daemon=True).start()

    # Prüfen, ob die Datei seit dem letzten Laden geändert wurde
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
        
    with _cache_lock:
        if _tasks_cache is None or mtime > _last_cache_update:
            _tasks_cache = read_json(path, {})
            _last_cache_update = mtime
        return _tasks_cache

def _notify_task_update(tid: str):
    with _subscribers_lock:
        for q in _task_subscribers:
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

@tasks_bp.route("/propose", methods=["POST"])
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

@tasks_bp.route("/execute", methods=["POST"])
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
    tasks = _get_tasks_cache()
    return jsonify(list(tasks.values()))

@tasks_bp.route("/tasks", methods=["POST"])
@check_auth
def create_task():
    data = request.get_json()
    tid = data.get("id") or str(int(time.time()))
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
    # Logik für Task-Zuweisung
    _update_local_task_status(tid, "assigned", assigned_to=current_app.config["AGENT_NAME"])
    return jsonify({"status": "assigned", "agent": current_app.config["AGENT_NAME"]})

@tasks_bp.route("/tasks/<tid>/propose", methods=["POST"])
@check_auth
def task_propose(tid):
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    
    cfg = current_app.config["AGENT_CONFIG"]
    base_prompt = task.get("description") or task.get("prompt") or "Bearbeite Task " + tid
    
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

@tasks_bp.route("/tasks/<tid>/execute", methods=["POST"])
@check_auth
def task_execute(tid):
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    
    proposal = task.get("last_proposal")
    if not proposal:
        return jsonify({"error": "no_proposal"}), 400
    
    command = proposal.get("command")
    shell = get_shell()
    output, exit_code = shell.execute(command)
    
    history = task.get("history", [])
    history.append({
        "prompt": task.get("description"),
        "reason": proposal.get("reason"),
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
            _task_subscribers.append(q)
        
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
                    # Wir warten bis zu 15 Sekunden auf ein Update
                    updated_tid = q.get(timeout=15)
                except Empty:
                    yield ": keep-alive\n\n"
        finally:
            with _subscribers_lock:
                _task_subscribers.remove(q)
                
    return Response(generate(), mimetype="text/event-stream")
