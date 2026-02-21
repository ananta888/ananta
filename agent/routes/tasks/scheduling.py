from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.scheduler import get_scheduler

scheduling_bp = Blueprint("tasks_scheduling", __name__)


@scheduling_bp.route("/schedule", methods=["POST"])
@check_auth
def schedule_task():
    """
    Geplante Aufgabe hinzufügen
    ---
    parameters:
      - in: body
        name: body
        schema:
          properties:
            command:
              type: string
            interval_seconds:
              type: integer
    responses:
      201:
        description: Aufgabe geplant
    """
    data = request.json
    command = data.get("command")
    interval = data.get("interval_seconds")

    if not command or not interval:
        return api_response(status="error", message="command and interval_seconds are required", code=400)

    scheduler = get_scheduler()
    task = scheduler.add_task(command, int(interval))
    return api_response(data=task.model_dump(), code=201)


@scheduling_bp.route("/schedule", methods=["GET"])
@check_auth
def list_scheduled_tasks():
    """
    Alle geplanten Aufgaben auflisten
    ---
    responses:
      200:
        description: Liste der geplanten Aufgaben
    """
    scheduler = get_scheduler()
    return api_response(data=[t.model_dump() for t in scheduler.tasks])


@scheduling_bp.route("/schedule/<task_id>", methods=["DELETE"])
@check_auth
def remove_scheduled_task(task_id):
    """
    Geplante Aufgabe entfernen
    ---
    parameters:
      - name: task_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Entfernt
    """
    scheduler = get_scheduler()
    scheduler.remove_task(task_id)
    return api_response(data={"status": "deleted"})
