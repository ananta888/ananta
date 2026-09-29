"""Visual Process Designer API.

GET  /api/visual-process/presets                — list presets
GET  /api/visual-process/presets/<id>           — get preset graph
GET  /api/visual-process/skill-profiles         — agent library (VPAD-005)
GET  /api/visual-process/task-kinds             — canonical task kind list (VPWRK-001)
POST /api/visual-process/validate               — validate a graph
POST /api/visual-process/classify-step          — classify a single step
POST /api/visual-process/dry-run                — validate + blueprint mapping
POST /api/visual-process/mermaid                — Mermaid export
POST /api/visual-process/policy-summary         — policy/security summary
POST /api/visual-process/assemble-context       — context for one step
POST /api/visual-process/bpmn/import            — BPMN XML to graph
POST /api/visual-process/bpmn/export            — graph to BPMN XML
POST /api/visual-process/workflow-request       — graph to canonical workflow request
POST /api/visual-process/workflow/preflight     — authenticated read-only start advisory
POST /api/visual-process/workflow/start         — start through configured backend
POST /api/visual-process/workflow/<id>/message   — signed BPMN message through Hub receipts
POST /api/visual-process/workflow/<id>/resume   — resume through Hub control
POST /api/visual-process/workflow/<id>/retry    — retry through Hub control
POST /api/visual-process/workflow/<id>/caseflow-edge-trace — authorized edge trace read model
POST /api/visual-process/save-blueprint         — save dry-run result as Blueprint (VPBLUEPR-001)

-- Graph persistence (VPPERS-001) --
POST   /api/visual-process/graphs               — save new graph
GET    /api/visual-process/graphs               — list saved graphs
GET    /api/visual-process/graphs/<id>          — load graph
PUT    /api/visual-process/graphs/<id>          — update graph
DELETE /api/visual-process/graphs/<id>          — delete graph

Module layout -- this module is the public entry point; importing it registers
every route on ``vp_bp``. The implementation lives in single-responsibility
siblings:

* :mod:`agent.routes.visual_process_blueprint` - the shared ``vp_bp`` blueprint.
* :mod:`agent.routes.visual_process_route_dependencies` - the workflow handlers'
  collaborators and their per-application override seam.
* :mod:`agent.routes.visual_process_graph_support` - graph parsing, ownership,
  revision archiving and definition-save responses.
* :mod:`agent.routes.visual_process_model_plan` - workflow options/compilation and
  the per-step model routing plan.
* :mod:`agent.routes.visual_process_catalog_routes` - presets, skill profiles,
  task kinds and node definitions.
* :mod:`agent.routes.visual_process_design_routes` - validation, dry-run, model
  routing, location, blueprint save, BPMN import/export and graph exports.
* :mod:`agent.routes.visual_process_graph_routes` - graph persistence (v1/v2).
* :mod:`agent.routes.visual_process_workflow_routes` - workflow request,
  preflight, start, status and cancel.
* :mod:`agent.routes.visual_process_workflow_command_routes` - signal, message,
  resume and retry commands.
* :mod:`agent.routes.visual_process_workflow_event_routes` - events, event stream
  and caseflow edge trace.

The workflow handlers' collaborators (``configured_workflow_backend``,
``backend_error``, ``require_workflow_owner``, ``workflow_route_authorization_service``,
``get_caseflow_agent_collaboration_trace_projection_service``) are bundled in
:class:`agent.routes.visual_process_route_dependencies.VisualProcessRouteDependencies`
and resolved per application through ``VISUAL_PROCESS_ROUTE_DEPENDENCIES``; tests
replace them with ``VISUAL_PROCESS_ROUTE_DEPENDENCIES.override(app, ...)``. The
names re-exported below are kept for import compatibility only.
"""

from __future__ import annotations

from agent.routes.visual_process_blueprint import (
    vp_bp,
)
from agent.routes.visual_process_catalog_routes import (
    _node_definition_registry_response,
    get_preset_by_id,
    get_presets,
    node_definition,
    node_definitions,
    skill_profile_detail,
    skill_profiles,
    task_kinds,
)
from agent.routes.visual_process_design_routes import (
    assemble_context,
    bpmn_capabilities,
    bpmn_export,
    bpmn_import,
    dry_run,
    estimate_model_cost,
    mermaid,
    policy_summary_route,
    save_blueprint,
    validate,
    validate_model_routing,
    workflow_location,
)
from agent.routes.visual_process_graph_routes import (
    _save_graph_v2_impl,
    delete_graph,
    list_graphs,
    load_graph,
    save_graph,
    save_graph_v2,
    update_graph,
    update_graph_v2,
)
from agent.routes.visual_process_graph_support import (
    _archive_graph_revision,
    _definition_error,
    _definition_preconditions,
    _definition_save_response,
    _graph_principal,
    _owned_graph_model,
    _parse_graph,
    _validator,
)
from agent.routes.visual_process_model_plan import (
    _build_model_plan,
    _compile_workflow_request,
    _effective_step_routing,
    _invalid_model_plan,
    _workflow_options,
)
from agent.routes.visual_process_workflow_command_routes import (
    _dispatch_workflow_signal,
    _named_workflow_control,
    _public_command_rejection_reason,
    _workflow_command_id,
    _workflow_command_target_bindings,
    workflow_message,
    workflow_resume,
    workflow_retry,
    workflow_signal,
)
from agent.routes.visual_process_workflow_event_routes import (
    caseflow_edge_trace,
    workflow_event_stream,
    workflow_events,
)
from agent.routes.visual_process_workflow_routes import (
    _principal_workflow_request,
    _workflow_start_request,
    workflow_cancel,
    workflow_preflight,
    workflow_request,
    workflow_start,
    workflow_status,
)
from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    get_caseflow_agent_collaboration_trace_projection_service,
)
from agent.services.workflow_route_authorization_service import workflow_route_authorization_service

from .workflow_control_security import (
    backend_error,
    configured_workflow_backend,
    require_workflow_owner,
)

__all__ = [
    "_archive_graph_revision",
    "_build_model_plan",
    "_compile_workflow_request",
    "_definition_error",
    "_definition_preconditions",
    "_definition_save_response",
    "_dispatch_workflow_signal",
    "_effective_step_routing",
    "_graph_principal",
    "_invalid_model_plan",
    "_named_workflow_control",
    "_node_definition_registry_response",
    "_owned_graph_model",
    "_parse_graph",
    "_principal_workflow_request",
    "_public_command_rejection_reason",
    "_save_graph_v2_impl",
    "_validator",
    "_workflow_command_id",
    "_workflow_command_target_bindings",
    "_workflow_options",
    "_workflow_start_request",
    "assemble_context",
    "backend_error",
    "bpmn_capabilities",
    "bpmn_export",
    "bpmn_import",
    "caseflow_edge_trace",
    "configured_workflow_backend",
    "delete_graph",
    "dry_run",
    "estimate_model_cost",
    "get_caseflow_agent_collaboration_trace_projection_service",
    "get_preset_by_id",
    "get_presets",
    "list_graphs",
    "load_graph",
    "mermaid",
    "node_definition",
    "node_definitions",
    "policy_summary_route",
    "require_workflow_owner",
    "save_blueprint",
    "save_graph",
    "save_graph_v2",
    "skill_profile_detail",
    "skill_profiles",
    "task_kinds",
    "update_graph",
    "update_graph_v2",
    "validate",
    "validate_model_routing",
    "vp_bp",
    "workflow_cancel",
    "workflow_event_stream",
    "workflow_events",
    "workflow_location",
    "workflow_message",
    "workflow_preflight",
    "workflow_request",
    "workflow_resume",
    "workflow_retry",
    "workflow_route_authorization_service",
    "workflow_signal",
    "workflow_start",
    "workflow_status",
]
