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
    _extract_tool_calls, _get_approved_command, _log_terminal_entry, rate_limit, _http_post, _archive_old_tasks
)
from agent.llm_integration import _call_llm
from agent.models import (
    TaskStepProposeRequest, TaskStepProposeResponse, 
    TaskStepExecuteRequest, TaskStepExecuteResponse,
    TaskDelegationRequest
)
from agent.metrics import TASK_RECEIVED, TASK_COMPLETED, TASK_FAILED, RETRIES_TOTAL
from agent.shell import get_shell
from agent.auth import check_auth
from agent.scheduler import get_scheduler
from agent.tools import registry as tool_registry
from agent.repository import (
    task_repo, scheduled_task_repo, agent_repo, role_repo, template_repo, team_member_repo
)
from agent.db_models import TaskDB, ScheduledTaskDB, RoleDB, TemplateDB, TeamMemberDB

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
    tasks = task_repo.get_all()
    return {t.id: t.dict() for t in tasks}

def _notify_task_update(tid: str):
    with _subscribers_lock:
        for subscriber_tid, q in _task_subscribers:
            if subscriber_tid == tid or subscriber_tid == "*":
                q.put(tid)

def _get_local_task_status(tid: str):
    task = task_repo.get_by_id(tid)
    return task.dict() if task else None

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
                logging.error(f"Fehler beim Senden des Webhooks an {task['callback_url']}: {e}")

        threading.Thread(target=send_callback, daemon=True).start()

def _forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str = None) -> Any:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
    return _http_post(url, data=data, headers=headers)

def _get_system_prompt_for_task(tid: str) -> Optional[str]:
    from agent.repository import team_repo
    task = task_repo.get_by_id(tid)
    if not task:
        return None
    
    role_id = task.assigned_role_id
    template_id = None
    
    # Falls keine Rolle direkt zugewiesen, versuchen wir sie über den Agenten und das Team zu finden
    if task.team_id and task.assigned_agent_url:
        members = team_member_repo.get_by_team(task.team_id)
        for m in members:
            if m.agent_url == task.assigned_agent_url:
                if not role_id:
                    role_id = m.role_id
                template_id = getattr(m, "custom_template_id", None)
                break
                
    if role_id and not template_id:
        role = role_repo.get_by_id(role_id)
        if role:
            template_id = role.default_template_id
            
    if template_id:
        template = template_repo.get_by_id(template_id)
        if template:
            prompt = template.prompt_template
            
            # Variablen ersetzen
            variables = {
                "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
                "task_title": task.title or "Kein Titel",
                "task_description": task.description or "Keine Beschreibung"
            }
            
            if task.team_id:
                team = team_repo.get_by_id(task.team_id)
                if team:
                    variables["team_name"] = team.name
            
            if role_id:
                role = role_repo.get_by_id(role_id)
                if role:
                    variables["role_name"] = role.name
            
            for k, v in variables.items():
                prompt = prompt.replace("{{" + k + "}}", str(v))
                
            return prompt
    
    return None

def _run_async_propose(app_instance, tid: str, provider: str, model: str, prompt: str, urls: dict, api_key: str, history: list, agent_name: str):
    with app_instance.app_context():
        try:
            raw_res = _call_llm(
                provider=provider,
                model=model,
                prompt=prompt,
                urls=urls,
                api_key=api_key,
                history=history
            )
            
            if not raw_res:
                raise RuntimeError("LLM-Aufruf lieferte kein Ergebnis (Timeout oder Fehler).")
            
            reason = _extract_reason(raw_res)
            command = _extract_command(raw_res)
            tool_calls = _extract_tool_calls(raw_res)
            
            proposal = {"reason": reason}
            if command and command != raw_res.strip():
                proposal["command"] = command
            if tool_calls:
                proposal["tool_calls"] = tool_calls

            _update_local_task_status(tid, "proposing", last_proposal=proposal)
            
            _log_terminal_entry(agent_name, 0, "in", prompt=prompt, task_id=tid)
            _log_terminal_entry(agent_name, 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
            
            logging.info(f"Asynchroner Vorschlag für Task {tid} abgeschlossen.")
        except Exception as e:
            logging.error(f"Fehler bei asynchronem Vorschlag für Task {tid}: {e}")
            try:
                _update_local_task_status(tid, "failed", error=str(e))
            except Exception as e2:
                logging.error(f"Fehler beim Setzen des Fehlerstatus für Task {tid}: {e2}")

@tasks_bp.route("/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def propose_step():
    data: TaskStepProposeRequest = g.validated_data
    cfg = current_app.config["AGENT_CONFIG"]
    
    provider = data.provider or cfg.get("provider", "ollama")
    model = data.model or cfg.get("model", "llama3")
    prompt = data.prompt or "Was soll ich als nächstes tun?"
    
    # Synchron ausführen (für Abwärtskompatibilität mit Tests und einfachen Clients)
    raw_res = _call_llm(
        provider=provider,
        model=model,
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"]
    )
    
    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)
    
    if data.task_id:
        proposal = {"reason": reason}
        if command and command != raw_res.strip():
            proposal["command"] = command
        if tool_calls:
            proposal["tool_calls"] = tool_calls
            
        _update_local_task_status(data.task_id, "proposing", last_proposal=proposal)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=data.task_id)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=data.task_id)

    return jsonify(TaskStepProposeResponse(
        reason=reason,
        command=command if command != raw_res.strip() else None,
        tool_calls=tool_calls,
        raw=raw_res
    ).model_dump())

@tasks_bp.route("/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def execute_step():
    data: TaskStepExecuteRequest = g.validated_data
    
    output_parts = []
    overall_exit_code = 0
    
    if data.tool_calls:
        for tc in data.tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            tool_res = tool_registry.execute(name, args)
            res_str = f"Tool '{name}': {'Erfolg' if tool_res.success else 'Fehler'}"
            if tool_res.output: res_str += f"\nOutput: {tool_res.output}"
            if tool_res.error:
                res_str += f"\nError: {tool_res.error}"
                overall_exit_code = 1
            output_parts.append(res_str)

    if data.command:
        shell = get_shell()
        output, exit_code = shell.execute(data.command, timeout=data.timeout or 60)
        output_parts.append(output)
        if exit_code != 0:
            overall_exit_code = exit_code
    
    final_output = "\n---\n".join(output_parts)
    final_exit_code = overall_exit_code

    # Persistenz falls task_id vorhanden
    if data.task_id:
        status = "completed" if final_exit_code == 0 else "failed"
        _update_local_task_status(data.task_id, status, last_output=final_output, last_exit_code=final_exit_code)
        if status == "completed": TASK_COMPLETED.inc()
        else: TASK_FAILED.inc()

    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", command=data.command, tool_calls=data.tool_calls, task_id=data.task_id)
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", output=final_output, exit_code=final_exit_code, task_id=data.task_id)
    
    return jsonify(TaskStepExecuteResponse(
        output=final_output,
        exit_code=final_exit_code,
        task_id=data.task_id,
        status="completed" if final_exit_code == 0 else "failed"
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

@tasks_bp.route("/tasks/<tid>/delegate", methods=["POST"])
@check_auth
@validate_request(TaskDelegationRequest)
def delegate_task(tid):
    """Delegiert einen Sub-Task an einen anderen Agenten."""
    data: TaskDelegationRequest = g.validated_data
    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return jsonify({"error": "parent_task_not_found"}), 404

    subtask_id = f"sub-{uuid.uuid4()}"
    
    # Eigene URL für Callback bestimmen
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    callback_url = f"{my_url.rstrip('/')}/tasks/{tid}/subtask-callback"
    
    # Task auf dem anderen Agenten erstellen
    delegation_payload = {
        "id": subtask_id,
        "description": data.subtask_description,
        "parent_task_id": tid,
        "priority": data.priority,
        "callback_url": callback_url,
        "callback_token": settings.api_token
    }
    
    try:
        res = _forward_to_worker(
            data.agent_url,
            "/tasks",
            delegation_payload,
            token=data.agent_token
        )
        
        # Lokalen Parent-Task aktualisieren mit Info über den Sub-Task
        subtasks = parent_task.get("subtasks", [])
        subtasks.append({
            "id": subtask_id,
            "agent_url": data.agent_url,
            "description": data.subtask_description,
            "status": "created"
        })
        _update_local_task_status(tid, parent_task.get("status", "in_progress"), subtasks=subtasks)
        
        return jsonify({
            "status": "delegated",
            "subtask_id": subtask_id,
            "agent_url": data.agent_url,
            "response": res
        })
    except Exception as e:
        logging.error(f"Delegation an {data.agent_url} fehlgeschlagen: {e}")
        return jsonify({"error": "delegation_failed", "message": str(e)}), 502

@tasks_bp.route("/tasks/<tid>/subtask-callback", methods=["POST"])
@check_auth
def subtask_callback(tid):
    """Callback-Endpunkt für Status-Updates von Sub-Tasks."""
    data = request.get_json()
    subtask_id = data.get("id")
    new_status = data.get("status")
    
    if not subtask_id or not new_status:
        return jsonify({"error": "invalid_payload"}), 400
        
    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return jsonify({"error": "parent_task_not_found"}), 404
        
    subtasks = parent_task.get("subtasks", [])
    updated = False
    for st in subtasks:
        if st.get("id") == subtask_id:
            st["status"] = new_status
            # Optional: weitere Infos vom Worker übernehmen
            if "last_output" in data:
                st["last_output"] = data["last_output"]
            if "last_exit_code" in data:
                st["last_exit_code"] = data["last_exit_code"]
            updated = True
            break
            
    if updated:
        # Status des Parents beibehalten, nur Subtasks aktualisieren
        _update_local_task_status(tid, parent_task.get("status", "in_progress"), subtasks=subtasks)
        return jsonify({"status": "updated"})
    else:
        return jsonify({"error": "subtask_not_found"}), 404

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
    
    # Tool-Definitionen für den Prompt
    tools_desc = json.dumps(tool_registry.get_tool_definitions(), indent=2, ensure_ascii=False)

    system_prompt = _get_system_prompt_for_task(tid)
    if system_prompt:
        prompt = (
            f"{system_prompt}\n\n"
            f"Aktueller Auftrag: {base_prompt}\n\n"
            f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n\n"
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            "  \"reason\": \"Kurze Begründung\",\n"
            "  \"command\": \"Shell-Befehl (optional)\",\n"
            "  \"tool_calls\": [ { \"name\": \"tool_name\", \"args\": { \"arg1\": \"val1\" } } ] (optional)\n"
            "}"
        )
    else:
        prompt = (
            f"{base_prompt}\n\n"
            f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n\n"
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            "  \"reason\": \"Kurze Begründung\",\n"
            "  \"command\": \"Shell-Befehl (optional)\",\n"
            "  \"tool_calls\": [ { \"name\": \"tool_name\", \"args\": { \"arg1\": \"val1\" } } ] (optional)\n"
            "}"
        )
    
    # Synchron ausführen
    raw_res = _call_llm(
        provider=data.provider or cfg.get("provider", "ollama"),
        model=data.model or cfg.get("model", "llama3"),
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"],
        history=task.get("history", [])
    )
    
    if not raw_res:
        return jsonify({"error": "llm_failed"}), 502

    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)
    
    proposal = {"reason": reason}
    if command and command != raw_res.strip(): # Nur wenn es wirklich wie ein Befehl aussieht
        proposal["command"] = command
    if tool_calls:
        proposal["tool_calls"] = tool_calls

    _update_local_task_status(tid, "proposing", last_proposal=proposal)
    
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=tid)
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
    
    return jsonify({
        "status": "proposing",
        "reason": reason,
        "command": command if command != raw_res.strip() else None,
        "tool_calls": tool_calls,
        "raw": raw_res
    })

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
    tool_calls = data.tool_calls
    reason = "Direkte Ausführung"
    
    if not command and not tool_calls:
        proposal = task.get("last_proposal")
        if not proposal:
            return jsonify({"error": "no_proposal"}), 400
        command = proposal.get("command")
        tool_calls = proposal.get("tool_calls")
        reason = proposal.get("reason", "Vorschlag ausgeführt")
    
    output_parts = []
    overall_exit_code = 0
    
    # 1. Tool Calls ausführen
    if tool_calls:
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            current_app.logger.info(f"Task {tid} führt Tool aus: {name} mit {args}")
            tool_res = tool_registry.execute(name, args)
            
            res_str = f"Tool '{name}': {'Erfolg' if tool_res.success else 'Fehler'}"
            if tool_res.output:
                res_str += f"\nOutput: {tool_res.output}"
            if tool_res.error:
                res_str += f"\nError: {tool_res.error}"
                overall_exit_code = 1
            
            output_parts.append(res_str)

    # 2. Shell Command ausführen
    if command:
        shell = get_shell()
        retries_left = data.retries or 0
        cmd_output, cmd_exit_code = "", -1
        
        while True:
            cmd_output, cmd_exit_code = shell.execute(command, timeout=data.timeout or 60)
            if cmd_exit_code == 0 or retries_left <= 0:
                break
            
            retries_left -= 1
            RETRIES_TOTAL.inc()
            logging.info(f"Task {tid} Shell-Fehler (exit_code {cmd_exit_code}). Wiederholung... ({retries_left} übrig)")
            time.sleep(data.retry_delay or 1)
        
        output_parts.append(cmd_output)
        if cmd_exit_code != 0:
            overall_exit_code = cmd_exit_code

    output = "\n---\n".join(output_parts)
    exit_code = overall_exit_code

    history = task.get("history", [])
    history.append({
        "prompt": task.get("description"),
        "reason": reason,
        "command": command,
        "tool_calls": tool_calls,
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

@tasks_bp.route("/schedule", methods=["POST"])
@check_auth
def schedule_task():
    """Plant einen neuen periodischen Task."""
    data = request.json
    command = data.get("command")
    interval = data.get("interval_seconds")
    
    if not command or not interval:
        return jsonify({"error": "command and interval_seconds are required"}), 400
    
    scheduler = get_scheduler()
    task = scheduler.add_task(command, int(interval))
    return jsonify(task.dict()), 201

@tasks_bp.route("/schedule", methods=["GET"])
@check_auth
def list_scheduled_tasks():
    """Listet alle geplanten Tasks auf."""
    scheduler = get_scheduler()
    return jsonify([t.dict() for t in scheduler.tasks])

@tasks_bp.route("/schedule/<task_id>", methods=["DELETE"])
@check_auth
def remove_scheduled_task(task_id):
    """Entfernt einen geplanten Task."""
    scheduler = get_scheduler()
    scheduler.remove_task(task_id)
    return jsonify({"status": "deleted"}), 200
