"""Additional controller routes and ControllerAgent hooks.

This module defines the blueprint used by ``controller/controller.py``.  The
implementation was previously cluttered with duplicate blueprint definitions and
test endpoints that were not part of the documented API.  The rewritten module
exposes only the routes described in the documentation: ``next-task``,
``blacklist``, ``status`` and ``models``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .agent import ControllerAgent
from ..models import ModelPool

# ControllerAgent instance managing internal tasks/blacklist/logs
controller_agent = ControllerAgent(
    name="controller", model="none", prompt_template="", config_path=""
)

# Blueprint registered by ``controller/controller.py``
bp = Blueprint("controller", __name__, url_prefix="/controller")

# ModelPool used to track concurrency limits for provider/model combinations
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


__all__ = ["bp"]

