from flask import Blueprint
from agent.routes.tasks.execution import execution_bp
from agent.routes.tasks.management import management_bp
from agent.routes.tasks.logging import logging_bp
from agent.routes.tasks.scheduling import scheduling_bp
from agent.routes.tasks.autopilot import autopilot_bp, init_autopilot

# Haupt-Blueprint für Tasks, der alle Teil-Blueprints zusammenfasst
tasks_bp = Blueprint("tasks", __name__)


def register_tasks_blueprints(app):
    app.register_blueprint(execution_bp)
    app.register_blueprint(management_bp)
    app.register_blueprint(logging_bp)
    app.register_blueprint(scheduling_bp)
    app.register_blueprint(autopilot_bp)
    init_autopilot()


# Exportiere die Teil-Blueprints für den Fall, dass sie einzeln benötigt werden
__all__ = [
    "tasks_bp",
    "execution_bp",
    "management_bp",
    "logging_bp",
    "scheduling_bp",
    "autopilot_bp",
    "register_tasks_blueprints",
]
