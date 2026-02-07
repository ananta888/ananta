import uuid
import time
import logging
from flask import Blueprint, jsonify, request, g
from agent.auth import check_auth
from agent.repository import task_repo, agent_repo, role_repo, template_repo, team_member_repo
from agent.db_models import TaskDB
from agent.utils import validate_request, _http_post
from agent.models import TaskDelegationRequest
from agent.routes.tasks.utils import _update_local_task_status, _notify_task_update, _forward_to_worker, _get_tasks_cache, _get_local_task_status
from agent.metrics import TASK_RECEIVED
from agent.config import settings

management_bp = Blueprint("tasks_management", __name__)

@management_bp.route("/tasks", methods=["GET"])
@check_auth
def list_tasks():
    """
    Alle Tasks auflisten
    ---
    responses:
      200:
        description: Liste der Tasks
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

@management_bp.route("/tasks", methods=["POST"])
@check_auth
def create_task():
    """
    Neuen Task erstellen
    ---
    parameters:
      - in: body
        name: body
        schema:
          properties:
            id:
              type: string
            description:
              type: string
    responses:
      201:
        description: Task erstellt
    """
    data = request.get_json() or {}
    tid = data.get("id") or str(uuid.uuid4())
    status = data.get("status", "created")
    safe_data = {k: v for k, v in data.items() if k != "status"}
    _update_local_task_status(tid, status, **safe_data)
    TASK_RECEIVED.inc()
    return jsonify({"id": tid, "status": "created"}), 201

@management_bp.route("/tasks/<tid>", methods=["GET"])
@check_auth
def get_task(tid):
    """
    Task-Details abrufen
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Task-Details
      404:
        description: Nicht gefunden
    """
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
    return jsonify(task)

@management_bp.route("/tasks/<tid>", methods=["PATCH"])
@check_auth
def patch_task(tid):
    """
    Task aktualisieren
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
      - in: body
        name: body
        schema:
          properties:
            status:
              type: string
    responses:
      200:
        description: Task aktualisiert
    """
    data = request.get_json()
    _update_local_task_status(tid, data.get("status", "updated"), **data)
    return jsonify({"id": tid, "status": "updated"})

@management_bp.route("/tasks/<tid>/assign", methods=["POST"])
@check_auth
def assign_task(tid):
    """
    Task einem Agenten zuweisen
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
      - in: body
        name: body
        schema:
          properties:
            agent_url:
              type: string
    responses:
      200:
        description: Zugewiesen
    """
    data = request.get_json()
    agent_url = data.get("agent_url")
    agent_token = data.get("token")
    
    if not agent_url:
        return jsonify({"error": "agent_url_required"}), 400
        
    _update_local_task_status(tid, "assigned", assigned_agent_url=agent_url, assigned_agent_token=agent_token)
    return jsonify({"status": "assigned", "agent_url": agent_url})

@management_bp.route("/tasks/<tid>/unassign", methods=["POST"])
@check_auth
def unassign_task(tid):
    """
    Zuweisung aufheben
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Zuweisung aufgehoben
    """
    task = _get_local_task_status(tid)
    if not task:
        return jsonify({"error": "not_found"}), 404
        
    _update_local_task_status(tid, "todo", assigned_agent_url=None, assigned_agent_token=None, assigned_to=None)
    return jsonify({"status": "todo", "unassigned": True})

@management_bp.route("/tasks/<tid>/delegate", methods=["POST"])
@check_auth
@validate_request(TaskDelegationRequest)
def delegate_task(tid):
    """
    Task an anderen Agenten delegieren
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
      - in: body
        name: body
        schema:
          $ref: '#/definitions/TaskDelegationRequest'
    responses:
      200:
        description: Delegiert
    """
    data: TaskDelegationRequest = g.validated_data
    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return jsonify({"error": "parent_task_not_found"}), 404

    subtask_id = f"sub-{uuid.uuid4()}"
    
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    callback_url = f"{my_url.rstrip('/')}/tasks/{tid}/subtask-callback"
    
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

@management_bp.route("/tasks/<tid>/subtask-callback", methods=["POST"])
@check_auth
def subtask_callback(tid):
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
            if "last_output" in data:
                st["last_output"] = data["last_output"]
            if "last_exit_code" in data:
                st["last_exit_code"] = data["last_exit_code"]
            updated = True
            break
            
    if updated:
        _update_local_task_status(tid, parent_task.get("status", "in_progress"), subtasks=subtasks)
        return jsonify({"status": "updated"})
    else:
        return jsonify({"error": "subtask_not_found"}), 404
