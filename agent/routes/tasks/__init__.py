from flask import Blueprint

from agent.routes.tasks.auto_planner import auto_planner_bp, init_auto_planner
from agent.routes.tasks.autopilot import autopilot_bp, init_autopilot
from agent.routes.tasks.execution import execution_bp
from agent.routes.tasks.logging import logging_bp
from agent.routes.tasks.management import management_bp
from agent.routes.tasks.orchestration import orchestration_bp
from agent.routes.tasks.scheduling import scheduling_bp
from agent.routes.tasks.triggers import init_triggers, triggers_bp

tasks_bp = Blueprint("tasks", __name__)


def register_tasks_blueprints(app):
    app.register_blueprint(execution_bp)
    app.register_blueprint(management_bp)
    app.register_blueprint(orchestration_bp)
    app.register_blueprint(logging_bp)
    app.register_blueprint(scheduling_bp)
    app.register_blueprint(autopilot_bp)
    app.register_blueprint(auto_planner_bp)
    app.register_blueprint(triggers_bp)
    init_autopilot()
    init_auto_planner()
    init_triggers()


__all__ = [
    "tasks_bp",
    "execution_bp",
    "management_bp",
    "orchestration_bp",
    "logging_bp",
    "scheduling_bp",
    "autopilot_bp",
    "auto_planner_bp",
    "triggers_bp",
    "register_tasks_blueprints",
]
