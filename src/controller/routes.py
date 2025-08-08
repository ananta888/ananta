"""HTTP routes exposing controller functionality."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .agent import ControllerAgent
from ..models import ModelPool

controller_agent = ControllerAgent(
    name="controller",
    model="none",
    prompt_template="",
    config_path="",
)

bp = Blueprint("controller", __name__, url_prefix="/controller")

model_pool = ModelPool()


@bp.route("/next-task", methods=["GET"])
def next_task() -> object:
    """Return the next available task from the controller agent."""

    task = controller_agent.assign_task()
    return jsonify({"task": task})


@bp.route("/blacklist", methods=["GET", "POST"])
def blacklist() -> object:
    """Get or update the blacklist."""
import logging
from flask import Blueprint, request, jsonify

bp = Blueprint('controller', __name__, url_prefix='/controller')
logger = logging.getLogger(__name__)

@bp.route('/hello')
def hello():
    """Einfacher Test-Endpunkt."""
    return jsonify({"message": "Hello from controller blueprint!"})

@bp.route('/status')
def get_status():
    """Gibt den aktuellen Status des Controllers zurück."""
    return jsonify({
        "status": "running",
        "version": "1.0.0",
        "timestamp": "2025-08-08T06:30:00"
    })
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        entry = data.get("task")
        if entry:
            controller_agent.update_blacklist(entry)
        return ("", 204)
    return jsonify(sorted(controller_agent.blacklist))


@bp.route("/status", methods=["GET", "DELETE"])
def status() -> object:
    """Return or clear the controller agent log."""

    if request.method == "DELETE":
        controller_agent.clear_log()
        return ("", 204)
    return jsonify(controller_agent.log_status())


@bp.route("/models", methods=["GET", "POST"])
def models() -> object:
    """Inspect or update registered model limits."""

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        provider = data.get("provider")
        model = data.get("model")
        limit = data.get("limit", 1)
        if provider and model:
            model_pool.register(provider, model, int(limit))
            return ("", 204)
        return ("", 400)
    return jsonify(model_pool.status())
