import logging

from flask import Flask

from agent.bootstrap.route_aliases import register_route_aliases
from agent.routes.artifacts import artifacts_bp
from agent.routes.codecompass_domain_scope import codecompass_domain_scope_bp
from agent.routes.codecompass_graph import codecompass_graph_bp
from agent.routes.codecompass_reload import codecompass_reload_bp
from agent.routes.worker_tool_loop_diagnostics import worker_tool_loop_diagnostics_bp
from agent.routes.hub_direct_diagnostics import hub_direct_diagnostics_bp
from agent.routes.approvals import approvals_bp
from agent.routes.auth import auth_bp
from agent.routes.config import register_config_blueprints
from agent.routes.context_policy import context_policy_bp
from agent.routes.control_center_api import control_center_api_bp
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
from agent.routes.worker_pool import worker_pool_bp
from agent.routes.admin.planning_metrics import planning_metrics_bp
from agent.routes.admin.planning_dataset import planning_dataset_bp
from agent.routes.admin.planning_review import planning_review_bp
from agent.routes.sgpt import sgpt_bp
from agent.routes.system import system_bp
from agent.routes.voice import voice_bp
from agent.routes.tasks import register_tasks_blueprints, tasks_bp
from agent.routes.blueprint_routes import blueprint_bp
from agent.routes.teams import teams_bp
from agent.routes.webhooks import webhooks_bp
from agent.routes.sources import sources_bp
from agent.routes.goal_artifacts import goal_artifacts_bp
from agent.routes.debug.prompt_traces import prompt_traces_bp
from agent.routes.debug.prompt_render import prompt_render_bp
from agent.routes.debug.backend_observability import backend_observability_bp
from agent.routes.debug.command_guardrails import command_guardrails_bp
from agent.routes.terminal import terminal_bp
from agent.routes.auth_oidc import oidc_bp
from agent.routes.ai_snake_config import ai_snake_config_bp
from agent.routes.network_profiles import network_profiles_bp
from agent.routes.snakes import snakes_bp
from agent.routes.share_sessions import share_sessions_bp
from agent.routes.pair_groups import pair_groups_bp
from agent.routes.rendezvous import rendezvous_bp
from agent.routes.repair import repair_bp
from agent.routes.webrtc_signaling import webrtc_signaling_bp
from agent.routes.chat import chat_bp # New: Chat API
from agent.routes.config_graph import config_graph_bp
from agent.routes.effective_workflow import effective_workflow_bp
from agent.routes.diff3 import diff3_bp
from agent.routes.snapshot_diff_api import snapshot_diff_bp
from agent.routes.visual_process import vp_bp
from agent.routes.deterministic_run import det_run_bp
from agent.routes.wiki_graph import wiki_graph_bp
from agent.ws_terminal import register_ws_terminal


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(system_bp, url_prefix="/api/system")
    app.register_blueprint(demo_bp)
    register_config_blueprints(app)
    app.register_blueprint(hub_benchmark_bp, url_prefix="/api")
    app.register_blueprint(ollama_benchmark_bp, url_prefix="/api")
    app.register_blueprint(worker_pool_bp, url_prefix="/api")
    app.register_blueprint(planning_metrics_bp)
    app.register_blueprint(planning_dataset_bp)
    app.register_blueprint(planning_review_bp)
    app.register_blueprint(tasks_bp)
    register_tasks_blueprints(app)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(codecompass_domain_scope_bp)
    app.register_blueprint(codecompass_graph_bp)
    app.register_blueprint(codecompass_reload_bp)
    app.register_blueprint(worker_tool_loop_diagnostics_bp)
    app.register_blueprint(hub_direct_diagnostics_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(openai_compat_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(evolution_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(blueprint_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(context_policy_bp)
    app.register_blueprint(control_center_api_bp)
    app.register_blueprint(instruction_layers_bp)
    app.register_blueprint(blender_client_surface_bp, url_prefix="/api/client-surfaces/blender")
    app.register_blueprint(freecad_client_surface_bp, url_prefix="/api/client-surfaces/freecad")
    app.register_blueprint(integrations_workflows_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(goal_artifacts_bp)
    app.register_blueprint(sgpt_bp, url_prefix="/api/sgpt")
    app.register_blueprint(prompt_traces_bp)
    app.register_blueprint(prompt_render_bp)
    app.register_blueprint(backend_observability_bp)
    app.register_blueprint(command_guardrails_bp)
    app.register_blueprint(terminal_bp)
    app.register_blueprint(oidc_bp)
    app.register_blueprint(ai_snake_config_bp)
    app.register_blueprint(network_profiles_bp)
    app.register_blueprint(snakes_bp)
    app.register_blueprint(share_sessions_bp)
    app.register_blueprint(pair_groups_bp)
    app.register_blueprint(rendezvous_bp, url_prefix="/api")
    app.register_blueprint(repair_bp)
    app.register_blueprint(webrtc_signaling_bp, url_prefix="/api")
    app.register_blueprint(chat_bp) # New: Chat Sessions API
    app.register_blueprint(config_graph_bp)
    app.register_blueprint(effective_workflow_bp)
    app.register_blueprint(diff3_bp)
    app.register_blueprint(snapshot_diff_bp)
    app.register_blueprint(vp_bp)
    app.register_blueprint(det_run_bp)
    app.register_blueprint(wiki_graph_bp)
    register_ws_terminal(app)


def register_alias_routes(app: Flask) -> None:
    try:
        register_route_aliases(app)
    except Exception as e:
        logging.warning(f"Konnte Alias-Routen nicht registrieren: {e}")
