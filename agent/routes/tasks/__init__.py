from flask import Blueprint

import agent.routes.tasks.goals  # noqa: F401 — registers routes on goals_bp
from agent.routes.tasks.auto_planner import auto_planner_bp, init_auto_planner
from agent.routes.tasks.autopilot import autopilot_bp, init_autopilot
from agent.routes.tasks.execution import execution_bp
from agent.routes.tasks.goals_blueprint import goals_bp
from agent.routes.tasks.kanban import kanban_bp
from agent.routes.tasks.knowledge_index_snapshot import (
    knowledge_index_snapshot_bp,
)
from agent.routes.tasks.logging import logging_bp
from agent.routes.tasks.management import management_bp
from agent.routes.tasks.native_artifact_ingress import native_artifact_ingress_bp
from agent.routes.tasks.orchestration import orchestration_bp
from agent.routes.tasks.organization_research_intake import (
    organization_research_intake_bp,
)
from agent.routes.tasks.recovery_artifact_ingress import (
    recovery_artifact_ingress_bp,
)
from agent.routes.tasks.recovery_manifest import recovery_manifest_bp
from agent.routes.tasks.scheduling import scheduling_bp
from agent.routes.tasks.source_verification import source_verification_bp
from agent.routes.tasks.triggers import init_triggers, triggers_bp
from agent.routes.tasks.verification import verification_bp

tasks_bp = Blueprint("tasks", __name__)


def register_tasks_blueprints(app):
    if str(app.config.get("ROLE") or "").strip().lower() == "worker":
        from agent.auth import resolve_configured_agent_token
        from agent.config import settings
        from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
        from worker.runtime.native_graph.pi_evidence_runtime import consume_pi_evidence_task
        from worker.runtime.native_graph.result_store_runtime import configured_native_result_store_for_task

        app.register_blueprint(create_native_execution_blueprint(
            token_provider=lambda: resolve_configured_agent_token(app.config),
            recipient_provider=lambda: (settings.agent_name, settings.agent_url),
            result_store_resolver=configured_native_result_store_for_task,
            evidence_consumer=consume_pi_evidence_task,
        ))
    app.register_blueprint(kanban_bp)
    app.register_blueprint(execution_bp)
    app.register_blueprint(management_bp)
    app.register_blueprint(orchestration_bp)
    app.register_blueprint(organization_research_intake_bp)
    app.register_blueprint(knowledge_index_snapshot_bp)
    app.register_blueprint(recovery_manifest_bp)
    app.register_blueprint(recovery_artifact_ingress_bp)
    app.register_blueprint(native_artifact_ingress_bp)
    app.register_blueprint(logging_bp)
    app.register_blueprint(goals_bp)
    app.register_blueprint(scheduling_bp)
    app.register_blueprint(source_verification_bp)
    app.register_blueprint(verification_bp)
    app.register_blueprint(autopilot_bp)
    app.register_blueprint(auto_planner_bp)
    app.register_blueprint(triggers_bp)
    init_autopilot(app)
    init_auto_planner()
    init_triggers()


__all__ = [
    "tasks_bp",
    "execution_bp",
    "goals_bp",
    "management_bp",
    "orchestration_bp",
    "organization_research_intake_bp",
    "knowledge_index_snapshot_bp",
    "recovery_manifest_bp",
    "recovery_artifact_ingress_bp",
    "native_artifact_ingress_bp",
    "logging_bp",
    "kanban_bp",
    "scheduling_bp",
    "source_verification_bp",
    "verification_bp",
    "autopilot_bp",
    "auto_planner_bp",
    "triggers_bp",
    "register_tasks_blueprints",
]
