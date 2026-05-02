import logging

from flask import Flask

from agent.bootstrap.route_aliases import register_route_aliases
from agent.routes.artifacts import artifacts_bp
from agent.routes.auth import auth_bp
from agent.routes.config import register_config_blueprints
from agent.routes.demo import demo_bp
from agent.routes.evolution import evolution_bp
from agent.routes.blender_client_surface import blender_client_surface_bp
from agent.routes.freecad_client_surface import freecad_client_surface_bp
from agent.routes.hub_benchmark import hub_benchmark_bp
from agent.routes.instruction_layers import instruction_layers_bp
from agent.routes.integrations_workflows import integrations_workflows_bp
from agent.routes.knowledge import knowledge_bp
from agent.routes.mcp import mcp_bp
from agent.routes.ollama_benchmark import ollama_benchmark_bp
from agent.routes.openai_compat import openai_compat_bp
from agent.routes.sgpt import sgpt_bp
from agent.routes.system import system_bp
from agent.routes.voice import voice_bp
from agent.routes.tasks import register_tasks_blueprints, tasks_bp
from agent.routes.teams import teams_bp
from agent.routes.webhooks import webhooks_bp
from agent.ws_terminal import register_ws_terminal


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(system_bp, url_prefix="/api/system")
    app.register_blueprint(demo_bp)
    register_config_blueprints(app)
    app.register_blueprint(hub_benchmark_bp, url_prefix="/api")
    app.register_blueprint(ollama_benchmark_bp, url_prefix="/api")
    app.register_blueprint(tasks_bp)
    register_tasks_blueprints(app)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(openai_compat_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(evolution_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(instruction_layers_bp)
    app.register_blueprint(blender_client_surface_bp, url_prefix="/api/client-surfaces/blender")
    app.register_blueprint(freecad_client_surface_bp, url_prefix="/api/client-surfaces/freecad")
    app.register_blueprint(integrations_workflows_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(sgpt_bp, url_prefix="/api/sgpt")
    register_ws_terminal(app)


def register_alias_routes(app: Flask) -> None:
    try:
        register_route_aliases(app)
    except Exception as e:
        logging.warning(f"Konnte Alias-Routen nicht registrieren: {e}")
