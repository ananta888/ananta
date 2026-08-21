import logging
import os

from flask import Flask

from agent.bootstrap.project_lifecycle import configure_project_lifecycle
from agent.bootstrap.route_aliases import register_route_aliases
from agent.bootstrap.source_control_api import register_source_control_api
from agent.config import settings
from agent.routes.admin.planning_dataset import planning_dataset_bp
from agent.routes.admin.planning_metrics import planning_metrics_bp
from agent.routes.admin.planning_review import planning_review_bp
from agent.routes.admin.sfu_broadcast_feature_flags import (
    sfu_broadcast_feature_flags_bp,
)
from agent.routes.ai_snake_config import ai_snake_config_bp
from agent.routes.approvals import approvals_bp
from agent.routes.artifacts import artifacts_bp
from agent.routes.auth import auth_bp
from agent.routes.auth_oidc import oidc_bp
from agent.routes.blender_client_surface import blender_client_surface_bp
from agent.routes.blueprint_routes import blueprint_bp
from agent.routes.caseflow import caseflow_bp
from agent.routes.caseflow_discovery import discovery_bp
from agent.routes.chat import chat_bp  # New: Chat API
from agent.routes.classroom import classroom_bp
from agent.routes.codecompass_domain_scope import codecompass_domain_scope_bp
from agent.routes.codecompass_graph import codecompass_graph_bp
from agent.routes.codecompass_reload import codecompass_reload_bp
from agent.routes.codecompass_retrieve import codecompass_retrieve_bp
from agent.routes.codecompass_layers import codecompass_layers_bp
from agent.routes.config import register_config_blueprints
from agent.routes.config_graph import config_graph_bp
from agent.routes.context_policy import context_policy_bp
from agent.routes.control_center_api import control_center_api_bp
from agent.routes.debug.backend_observability import backend_observability_bp
from agent.routes.debug.command_guardrails import command_guardrails_bp
from agent.routes.debug.prompt_render import prompt_render_bp
from agent.routes.debug.prompt_traces import prompt_traces_bp
from agent.routes.demo import demo_bp
from agent.routes.deterministic_run import det_run_bp
from agent.routes.diff3 import diff3_bp
from agent.routes.effective_workflow import effective_workflow_bp
from agent.routes.evolution import evolution_bp
from agent.routes.freecad_client_surface import freecad_client_surface_bp
from agent.routes.goal_artifacts import goal_artifacts_bp
from agent.routes.hub_benchmark import hub_benchmark_bp
from agent.routes.hub_direct_diagnostics import hub_direct_diagnostics_bp
from agent.routes.instruction_layers import instruction_layers_bp
from agent.routes.integrations_workflows import integrations_workflows_bp
from agent.routes.job_applications import job_app_bp
from agent.routes.knowledge import knowledge_bp
from agent.routes.langgraph_checkpoint_internal import langgraph_checkpoint_internal_bp
from agent.routes.mail_control import mail_control_bp
from agent.routes.mcp import mcp_bp
from agent.routes.ml_intern_lora_runtime import ml_intern_lora_runtime_bp
from agent.routes.ml_intern_speech_adapters import ml_intern_speech_adapters_bp
from agent.routes.ml_intern_training import ml_intern_training_bp
from agent.routes.model_intelligence import model_intelligence_bp
from agent.routes.network_profiles import network_profiles_bp
from agent.routes.ollama_benchmark import ollama_benchmark_bp
from agent.routes.openai_compat import openai_compat_bp
from agent.routes.ops import ops_bp
from agent.routes.organization_blueprints import organization_blueprints_bp
from agent.routes.organization_bundles import organization_bundles_bp
from agent.routes.organization_goals import organization_goals_bp
from agent.routes.organization_instances import organization_instances_bp
from agent.routes.organization_planning import organization_planning_bp
from agent.routes.organization_role_activation import organization_role_activation_bp
from agent.routes.organization_runtime import organization_runtime_bp
from agent.routes.organization_source_catalogs import organization_source_catalogs_bp
from agent.routes.organization_topology import organization_topology_bp
from agent.routes.organization_topology_patches import organization_topology_patches_bp
from agent.routes.pair_groups import pair_groups_bp
from agent.routes.projects import projects_bp
from agent.routes.rendezvous import rendezvous_bp
from agent.routes.repair import repair_bp
from agent.routes.restricted_inference_management import restricted_inference_management_bp
from agent.routes.run_control import run_control_bp
from agent.routes.semantic_media_contracts import semantic_media_contracts_bp
from agent.routes.semantic_media_debug import semantic_media_debug_bp
from agent.routes.semantic_media_privacy import semantic_media_privacy_bp
from agent.routes.semantic_sfu_admission import semantic_sfu_admission_bp
from agent.routes.sgpt import sgpt_bp
from agent.routes.share_sessions import share_sessions_bp
from agent.routes.snakes import snakes_bp
from agent.routes.snapshot_diff_api import snapshot_diff_bp
from agent.routes.sources import sources_bp
from agent.routes.speech_adaptation_control import speech_adaptation_control_bp
from agent.routes.speech_evidence_consents import speech_evidence_consents_bp
from agent.routes.speech_evidence_sync import speech_evidence_sync_bp
from agent.routes.speech_reconciliation import speech_reconciliation_bp
from agent.routes.system import system_bp
from agent.routes.tasks import register_tasks_blueprints, tasks_bp
from agent.routes.teams import teams_bp
from agent.routes.terminal import terminal_bp
from agent.routes.text_quality import text_quality_bp
from agent.routes.vector_store_control import vector_store_control_bp
from agent.routes.visual_process import vp_bp
from agent.routes.visual_process_assistant import visual_process_assistant_bp
from agent.routes.voice import voice_bp
from agent.routes.voice_configuration import voice_configuration_bp
from agent.routes.voice_governance import voice_governance_bp
from agent.routes.voice_live_runs import voice_live_runs_bp
from agent.routes.webhooks import webhooks_bp
from agent.routes.webrtc_sfu_broadcast_commands import webrtc_sfu_broadcast_commands_bp
from agent.routes.webrtc_sfu_broadcast_operations import webrtc_sfu_broadcast_operations_bp
from agent.routes.webrtc_sfu_broadcast_quality import webrtc_sfu_broadcast_quality_bp
from agent.routes.webrtc_sfu_browser_capabilities import webrtc_sfu_browser_capabilities_bp
from agent.routes.webrtc_sfu_layer_projections import webrtc_sfu_layer_projections_bp
from agent.routes.webrtc_sfu_node_enrollment import webrtc_sfu_node_enrollment_bp
from agent.routes.webrtc_sfu_node_observations import webrtc_sfu_node_observations_bp
from agent.routes.webrtc_signaling import webrtc_signaling_bp
from agent.routes.wiki_graph import wiki_graph_bp
from agent.routes.knowledge_hygiene import knowledge_hygiene_bp
from agent.routes.worker_pool import worker_pool_bp
from agent.routes.worker_tool_loop_diagnostics import worker_tool_loop_diagnostics_bp
from agent.routes.workflow_adapters import workflow_adapters_bp
from agent.routes.workflow_runtime_capabilities import workflow_runtime_capabilities_bp
from agent.routes.workflow_runtime_internal import workflow_runtime_internal_bp
from agent.routes.workflow_runtime_operations import workflow_runtime_operations_bp
from agent.routes.workflow_runtime_rollout import workflow_runtime_rollout_bp
from agent.routes.workflow_runtime_test_support import register_workflow_runtime_test_support
from agent.ws_terminal import register_ws_terminal
from agent.ws_voice import register_ws_voice


def register_blueprints(app: Flask) -> None:
    configure_project_lifecycle(app)
    app.register_blueprint(system_bp, url_prefix="/api/system")
    app.register_blueprint(demo_bp)
    register_config_blueprints(app)
    app.register_blueprint(hub_benchmark_bp, url_prefix="/api")
    app.register_blueprint(ollama_benchmark_bp, url_prefix="/api")
    app.register_blueprint(worker_pool_bp, url_prefix="/api")
    app.register_blueprint(planning_metrics_bp)
    app.register_blueprint(planning_dataset_bp)
    app.register_blueprint(planning_review_bp)
    app.register_blueprint(sfu_broadcast_feature_flags_bp)
    app.register_blueprint(tasks_bp)
    register_tasks_blueprints(app)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(codecompass_domain_scope_bp)
    app.register_blueprint(codecompass_graph_bp)
    app.register_blueprint(codecompass_reload_bp)
    app.register_blueprint(codecompass_retrieve_bp)
    app.register_blueprint(codecompass_layers_bp)
    app.register_blueprint(worker_tool_loop_diagnostics_bp)
    app.register_blueprint(hub_direct_diagnostics_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(knowledge_hygiene_bp)
    app.register_blueprint(ml_intern_training_bp)
    app.register_blueprint(model_intelligence_bp)
    app.register_blueprint(ml_intern_lora_runtime_bp)
    app.register_blueprint(ml_intern_speech_adapters_bp)
    app.register_blueprint(openai_compat_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(voice_configuration_bp)
    app.register_blueprint(voice_governance_bp)
    app.register_blueprint(voice_live_runs_bp)
    app.register_blueprint(restricted_inference_management_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(evolution_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(blueprint_bp)
    app.register_blueprint(organization_blueprints_bp)
    app.register_blueprint(organization_bundles_bp)
    app.register_blueprint(organization_instances_bp)
    app.register_blueprint(organization_goals_bp)
    app.register_blueprint(organization_source_catalogs_bp)
    app.register_blueprint(organization_planning_bp)
    app.register_blueprint(organization_role_activation_bp)
    app.register_blueprint(organization_runtime_bp)
    app.register_blueprint(organization_topology_bp)
    app.register_blueprint(organization_topology_patches_bp)
    app.register_blueprint(auth_bp)
    if settings.auth_test_endpoints_enabled and os.environ.get("RUN_SEMANTIC_MEDIA_LIVE_E2E", "").strip() == "1":
        # Imported only in the explicit live-test runtime so this route does
        # not exist in a normal Hub process, even as a hidden 404 endpoint.
        from agent.routes.semantic_media_e2e_support import semantic_media_e2e_support_bp

        app.register_blueprint(semantic_media_e2e_support_bp)
    app.register_blueprint(context_policy_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(control_center_api_bp)
    app.register_blueprint(instruction_layers_bp)
    app.register_blueprint(blender_client_surface_bp, url_prefix="/api/client-surfaces/blender")
    app.register_blueprint(freecad_client_surface_bp, url_prefix="/api/client-surfaces/freecad")
    app.register_blueprint(integrations_workflows_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(classroom_bp, url_prefix="/api/classroom")
    app.register_blueprint(text_quality_bp, url_prefix="/api")
    app.register_blueprint(sources_bp)
    register_source_control_api(app)
    app.register_blueprint(goal_artifacts_bp)
    app.register_blueprint(sgpt_bp, url_prefix="/api/sgpt")
    app.register_blueprint(semantic_media_contracts_bp)
    app.register_blueprint(semantic_media_debug_bp)
    app.register_blueprint(semantic_media_privacy_bp)
    app.register_blueprint(semantic_sfu_admission_bp)
    app.register_blueprint(speech_reconciliation_bp)
    app.register_blueprint(speech_adaptation_control_bp)
    app.register_blueprint(speech_evidence_consents_bp)
    app.register_blueprint(speech_evidence_sync_bp)
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
    app.register_blueprint(webrtc_sfu_broadcast_commands_bp)
    app.register_blueprint(webrtc_sfu_broadcast_operations_bp)
    app.register_blueprint(webrtc_sfu_broadcast_quality_bp)
    app.register_blueprint(webrtc_sfu_browser_capabilities_bp)
    app.register_blueprint(webrtc_sfu_layer_projections_bp)
    app.register_blueprint(webrtc_sfu_node_enrollment_bp)
    app.register_blueprint(webrtc_sfu_node_observations_bp)
    from agent.bootstrap.sfu_broadcast_final_composition import (
        register_sfu_broadcast_extension_blueprints,
    )

    register_sfu_broadcast_extension_blueprints(app)
    app.register_blueprint(chat_bp)  # New: Chat Sessions API
    app.register_blueprint(config_graph_bp)
    app.register_blueprint(effective_workflow_bp)
    app.register_blueprint(workflow_adapters_bp)
    app.register_blueprint(workflow_runtime_capabilities_bp)
    app.register_blueprint(langgraph_checkpoint_internal_bp)
    app.register_blueprint(workflow_runtime_internal_bp)
    app.register_blueprint(workflow_runtime_operations_bp)
    app.register_blueprint(workflow_runtime_rollout_bp)
    app.register_blueprint(vector_store_control_bp)
    app.register_blueprint(mail_control_bp)
    register_workflow_runtime_test_support(app)
    app.register_blueprint(diff3_bp)
    app.register_blueprint(snapshot_diff_bp)
    app.register_blueprint(vp_bp)
    app.register_blueprint(visual_process_assistant_bp)
    app.register_blueprint(det_run_bp)
    app.register_blueprint(wiki_graph_bp)
    app.register_blueprint(run_control_bp)
    app.register_blueprint(caseflow_bp)
    app.register_blueprint(discovery_bp)
    app.register_blueprint(job_app_bp)
    app.register_blueprint(ops_bp, url_prefix="/api/ops")
    register_ws_terminal(app)
    register_ws_voice(app)


def register_alias_routes(app: Flask) -> None:
    try:
        register_route_aliases(app)
    except Exception as e:
        logging.warning(f"Konnte Alias-Routen nicht registrieren: {e}")
