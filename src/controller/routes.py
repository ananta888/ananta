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

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        entry = data.get("task")
        if entry:
            controller_agent.update_blacklist(entry)
        return ("", 204)
    return jsonify(sorted(controller_agent.blacklist))


@bp.route("/status", methods=["GET"])
def status() -> object:
    """Return the controller agent log."""

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
