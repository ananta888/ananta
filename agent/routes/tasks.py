import os
import json
import time
import logging
from flask import Blueprint, jsonify, current_app, request, g, Response
from agent.utils import (
    validate_request, read_json, write_json, _extract_command, _extract_reason,
    _execute_command, _get_approved_command, _log_terminal_entry, rate_limit
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

def _get_local_task_status(tid: str):
    path = current_app.config["TASKS_PATH"]
    tasks = read_json(path, {})
    return tasks.get(tid)

def _update_local_task_status(tid: str, status: str, **kwargs):
    path = current_app.config["TASKS_PATH"]
    tasks = read_json(path, {})
    if tid not in tasks:
        tasks[tid] = {"id": tid, "created_at": time.time()}
    tasks[tid].update({"status": status, "updated_at": time.time(), **kwargs})
    write_json(path, tasks)

@tasks_bp.route("/propose", methods=["POST"])
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
    
    return jsonify(TaskStepProposeResponse(
        reason=reason,
        command=command,
        raw=raw_res
    ).dict())

@tasks_bp.route("/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def execute_step():
    data: TaskStepExecuteRequest = g.validated_data
    shell = get_shell()
    output, exit_code = shell.execute(data.command, timeout=data.timeout or 60)
    
    return jsonify(TaskStepExecuteResponse(
        output=output,
        exit_code=exit_code,
        task_id=data.task_id
    ).dict())

@tasks_bp.route("/logs", methods=["GET"])
def get_logs():
    log_file = os.path.join("data", "terminal_log.jsonl")
    if not os.path.exists(log_file):
        return jsonify([])
    
    logs = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                logs.append(json.loads(line))
            except Exception as e:
                logging.debug(f"Ignoriere ungültige Log-Zeile: {e}")
    return jsonify(logs[-100:]) # Letzte 100 Einträge

@tasks_bp.route("/tasks", methods=["GET"])
def list_tasks():
    tasks = read_json(current_app.config["TASKS_PATH"], {})
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
    prompt = task.get("description") or task.get("prompt") or "Bearbeite Task " + tid
    
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
    return jsonify({"reason": reason, "command": command})

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
    return jsonify({"output": output, "exit_code": exit_code, "status": status})

@tasks_bp.route("/tasks/<tid>/logs", methods=["GET"])
def task_logs(tid):
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    return jsonify(task.get("history", []))

@tasks_bp.route("/tasks/<tid>/stream-logs", methods=["GET"])
def stream_task_logs(tid):
    def generate():
        last_idx = 0
        while True:
            task = _get_local_task_status(tid)
            if not task: break
            history = task.get("history", [])
            if len(history) > last_idx:
                for i in range(last_idx, len(history)):
                    yield f"data: {json.dumps(history[i])}\n\n"
                last_idx = len(history)
            if task.get("status") in ("completed", "failed"):
                break
            time.sleep(1)
    return Response(generate(), mimetype="text/event-stream")
