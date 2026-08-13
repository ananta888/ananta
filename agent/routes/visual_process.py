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
POST /api/visual-process/workflow/start         — start through configured backend
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
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context
from sqlmodel import Session, select

from agent.auth import check_strict_auth, check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.redaction import VisibilityLevel, redact
from agent.config import settings
from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    CASEFLOW_EDGE_CATALOG_METADATA_KEY,
    MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES,
    CaseflowEdgeTraceProjectionError,
    CaseflowEdgeTraceQuery,
    get_caseflow_agent_collaboration_trace_projection_service,
)
from agent.services.chat_process_binding import authorize_graph, bind_graph_owner, public_graph
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_definition_service import (
    VisualProcessDefinitionConflict,
    VisualProcessDefinitionSecurityError,
    visual_process_definition_service,
)
from agent.services.visual_process_location_service import visual_process_location_service
from agent.services.workflow_backend import WorkflowRequest, WorkflowSignal
from agent.services.workflow_route_authorization_service import workflow_route_authorization_service
from agent.services.workflow_runtime._serialization import redact_json
from agent.services.workflow_runtime.streaming import (
    WorkflowStreamError,
    WorkflowStreamRequest,
    WorkflowStreamService,
)
from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict, graph_to_workflow_request
from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml
from agent.visual_process.context_assembly import StepContextAssembler
from agent.visual_process.mermaid_export import to_mermaid, to_tui_text
from agent.visual_process.models import VisualProcessGraph
from agent.visual_process.node_definitions import (
    NODE_REGISTRY_VERSION,
    get_node_definition,
    list_node_definitions,
)
from agent.visual_process.policy_hints import annotate_graph, policy_summary
from agent.visual_process.presets import get_preset, list_presets
from agent.visual_process.skill_profiles import get_skill_profile_registry
from agent.visual_process.step_executor import get_step_executor
from agent.visual_process.task_kind_registry import list_task_kinds
from agent.visual_process.validator import VisualProcessValidator

from .workflow_control_security import (
    MAX_WORKFLOW_CANCEL_BYTES,
    MAX_WORKFLOW_REQUEST_BYTES,
    MAX_WORKFLOW_SIGNAL_BYTES,
    backend_error,
    backend_result,
    configured_workflow_backend,
    require_workflow_owner,
    validate_workflow_id,
    workflow_json_body,
    workflow_principal,
)

vp_bp = Blueprint("visual_process", __name__, url_prefix="/api/visual-process")
_validator = VisualProcessValidator()


def _graph_principal() -> ChatSessionPrincipal | None:
    identity = dict(get_request_auth_context() or {})
    subject = identity.get("sub") or identity.get("username")
    tenant_id = (
        identity.get("tenant_id")
        or identity.get("tenant")
        or identity.get("organization_id")
        or subject
    )
    try:
        return ChatSessionPrincipal.from_values(tenant_id, subject)
    except ValueError:
        return None


def _owned_graph_model(
    graph: VisualProcessGraph,
    principal: ChatSessionPrincipal,
) -> VisualProcessGraph:
    return VisualProcessGraph.model_validate(bind_graph_owner(graph.model_dump(), principal))


def _archive_graph_revision(db: Session, row: VisualProcessGraphDB) -> None:
    try:
        data = json.loads(row.graph_json)
    except (TypeError, ValueError):
        return
    version = str(data.get("version") or "1.0")
    revision_id = f"{row.id}@{version}"
    if db.get(VisualProcessGraphDB, revision_id) is None:
        db.add(
            VisualProcessGraphDB(
                id=revision_id,
                name=row.name,
                description=row.description,
                tags=row.tags,
                graph_json=row.graph_json,
                definition_revision=row.definition_revision,
                base_graph_hash=row.base_graph_hash,
                graph_schema_version=row.graph_schema_version,
                node_registry_version=row.node_registry_version,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )


def _parse_graph() -> tuple[VisualProcessGraph | None, dict | None]:
    body = request.get_json(silent=True) or {}
    graph_data = body.get("graph") or body
    try:
        return VisualProcessGraph.model_validate(graph_data), None
    except Exception as exc:
        return None, {"error": "invalid_graph", "detail": str(exc)}


def _definition_preconditions(graph: VisualProcessGraph) -> tuple[int | None, str | None]:
    body = request.get_json(silent=True) or {}
    expected_raw = body.get("expected_revision")
    if expected_raw is None and isinstance(body.get("graph"), dict):
        expected_raw = body["graph"].get("definition_revision")
    if expected_raw is None and graph.definition_revision > 0:
        expected_raw = graph.definition_revision
    expected_revision: int | None
    try:
        expected_revision = int(expected_raw) if expected_raw is not None else None
    except (TypeError, ValueError):
        expected_revision = None

    expected_hash = str(body.get("base_graph_hash") or graph.base_graph_hash or "").strip() or None
    if isinstance(body.get("graph"), dict):
        expected_hash = str(body["graph"].get("base_graph_hash") or expected_hash or "").strip() or None
    if_match = str(request.headers.get("If-Match") or "").strip()
    if if_match:
        if if_match.startswith("W/"):
            if_match = if_match[2:]
        expected_hash = if_match.strip('"') or expected_hash
    return expected_revision, expected_hash


def _definition_error(exc: Exception):
    if isinstance(exc, VisualProcessDefinitionConflict):
        return jsonify(exc.as_dict()), 409
    if isinstance(exc, VisualProcessDefinitionSecurityError):
        status = 428 if exc.reason_code == "definition_precondition_required" else 422
        return jsonify(
            {"error": exc.reason_code, "error_code": exc.reason_code, "path": exc.path}
        ), status
    raise exc


def _definition_save_response(write, *, saved: bool = True):
    return jsonify(
        {
            "id": write.graph.id,
            "version": write.graph.version,
            "graph_schema_version": write.graph.graph_schema_version,
            "node_registry_version": write.graph.node_registry_version,
            "definition_revision": write.definition_revision,
            "base_graph_hash": write.base_graph_hash,
            "saved": saved,
            "changed": write.changed,
        }
    ), 200


def _workflow_options(body: dict) -> dict:
    return {
        "goal_id": body.get("goal_id") or body.get("goalId") or "",
        "plan_id": body.get("plan_id") or body.get("planId") or "",
        "blueprint_id": body.get("blueprint_id") or body.get("blueprintId"),
        "blueprint_version": body.get("blueprint_version") or body.get("blueprintVersion"),
        "workflow_type": body.get("workflow_type") or body.get("workflowType") or "visual_process",
        "policy_scope": body.get("policy_scope") or body.get("policyScope") or {"source": "visual_process"},
        "allowed_tools": body.get("allowed_tools") or body.get("allowedTools"),
        "requested_by": body.get("requested_by") or body.get("requestedBy") or "visual_process_designer",
    }


def _compile_workflow_request(graph: VisualProcessGraph, body: dict) -> WorkflowRequest:
    return graph_to_workflow_request(graph, **_workflow_options(body))


def _effective_step_routing(graph: VisualProcessGraph, step) -> dict:
    from agent.visual_process.models import ModelRoutingConfig

    merged: dict = {}
    graph_routing = ModelRoutingConfig.from_metadata(graph.metadata)
    step_routing = ModelRoutingConfig.from_metadata(step.metadata)
    if graph_routing is not None:
        merged.update(graph_routing.as_metadata())
    if step_routing is not None:
        merged.update(step_routing.as_metadata())
    return merged


def _build_model_plan(graph: VisualProcessGraph) -> dict:
    from agent.services.model_cost_estimator import ModelCostEstimator
    from agent.services.model_invocation_service import ModelInvocationService
    from agent.services.model_profile_resolver import RoutingContext

    try:
        resolver = ModelInvocationService._get_resolver()
    except Exception:
        resolver = None
    if resolver is None:
        return {
            "per_step_model_plan": [],
            "model_routing_summary": {"status": "not_configured", "total_estimated_cost": 0.0},
        }

    estimator = ModelCostEstimator()
    per_step: list[dict] = []
    total_cost = 0.0
    for step in graph.steps:
        routing = _effective_step_routing(graph, step)
        context_text = json.dumps({"graph": graph.metadata, "step": step.metadata}, sort_keys=True)
        ctx = RoutingContext(
            model_role=str(routing.get("model_role") or routing.get("default_model_role") or step.role or "any"),
            task_kind=step.kind,
            step_kind=step.kind,
            context_text=context_text,
            request_profile_id=routing.get("preferred_profile_id"),
            fallback_group_id=routing.get("fallback_group_id"),
            requires_json=bool(routing.get("requires_json", False)),
            requires_tools=bool(routing.get("requires_tools", False)),
            allow_cloud=bool(routing.get("allow_cloud", False)),
            max_estimated_cost_per_step=routing.get("max_estimated_cost"),
            metadata=routing,
        )
        result, chain = resolver.resolve_candidate_chain(ctx)
        selected = result.profile
        estimate = estimator.estimate_for_profile(selected, prompt_text=context_text).as_dict() if selected else None
        if estimate:
            total_cost += float(estimate["estimated_total_cost"])
        per_step.append(
            {
                "step_id": step.id,
                "model_role": ctx.model_role,
                "selected_profile_id": selected.profile_id if selected else None,
                "provider_id": selected.provider_id if selected else None,
                "model": selected.model if selected else None,
                "resolver_source": result.final_source,
                "resolver_rank": result.final_rank,
                "fallback_group_id": routing.get("fallback_group_id") or getattr(selected, "fallback_group", None),
                "context_recovery_strategies": list(routing.get("context_recovery_strategies") or []),
                "require_approval_for_generated_plan": bool(
                    routing.get("require_approval_for_generated_plan", True)
                ),
                "candidate_chain": [p.profile_id for p in chain],
                "cloud_allowed": bool(routing.get("allow_cloud", False)),
                "blocked_candidates": [
                    {"profile_id": pid, "reason": reason} for pid, reason in list(result.blocked_candidates or [])
                ],
                "policy_decisions": [
                    {
                        "rank": d.rank,
                        "source": d.source,
                        "profile_id": d.profile_id,
                        "accepted": d.accepted,
                        "reason": d.reason,
                    }
                    for d in list(result.decisions or [])
                ],
                "estimated_cost": estimate,
            }
        )
    return {
        "per_step_model_plan": per_step,
        "model_routing_summary": {
            "status": "ready",
            "step_count": len(per_step),
            "total_estimated_cost": round(total_cost, 8),
        },
    }


def _invalid_model_plan() -> dict[str, Any]:
    return {
        "per_step_model_plan": [],
        "model_routing_summary": {
            "status": "invalid",
            "total_estimated_cost": 0.0,
        },
    }


# ── Presets ───────────────────────────────────────────────────────────────────


@vp_bp.get("/presets")
def get_presets():
    return jsonify(list_presets()), 200


@vp_bp.get("/presets/<preset_id>")
def get_preset_by_id(preset_id: str):
    preset = get_preset(preset_id)
    if not preset:
        return jsonify({"error": "not_found"}), 404
    return jsonify(preset.model_dump()), 200


# ── Skill profiles (VPAD-005 agent library) ───────────────────────────────────


@vp_bp.get("/skill-profiles")
@check_user_auth
def skill_profiles():
    reg = get_skill_profile_registry()
    return jsonify(reg.as_library()), 200


@vp_bp.get("/skill-profiles/<profile_id>")
@check_user_auth
def skill_profile_detail(profile_id: str):
    reg = get_skill_profile_registry()
    p = reg.get(profile_id)
    if not p:
        return jsonify({"error": "not_found"}), 404
    return jsonify(p.as_dict()), 200


# ── Task kinds (VPWRK-001) ────────────────────────────────────────────────────


@vp_bp.get("/task-kinds")
def task_kinds():
    return jsonify(list_task_kinds()), 200


def _node_definition_registry_response():
    if not settings.visual_process_registry_inspector_enabled:
        return jsonify(
            {
                "error": "visual_process_registry_inspector_disabled",
                "error_code": "visual_process_registry_inspector_disabled",
            }
        ), 404
    definitions = list_node_definitions()
    canonical = json.dumps(
        {"registry_version": NODE_REGISTRY_VERSION, "definitions": definitions},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    registry_hash = hashlib.sha256(canonical).hexdigest()
    etag = f'"{registry_hash}"'
    if str(request.headers.get("If-None-Match") or "").strip() == etag:
        response = Response(status=304)
    else:
        response = jsonify(
            {
                "schema": "ananta.visual_process.node_definition_registry.v1",
                "registry_version": NODE_REGISTRY_VERSION,
                "registry_hash": registry_hash,
                "definitions": definitions,
            }
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=300, must-revalidate"
    return response


@vp_bp.get("/node-definitions")
@vp_bp.get("/v1/node-definitions")
@check_user_auth
def node_definitions():
    return _node_definition_registry_response()


@vp_bp.get("/node-definitions/<kind>")
@vp_bp.get("/v1/node-definitions/<kind>")
@check_user_auth
def node_definition(kind: str):
    if not settings.visual_process_registry_inspector_enabled:
        return jsonify(
            {
                "error": "visual_process_registry_inspector_disabled",
                "error_code": "visual_process_registry_inspector_disabled",
            }
        ), 404
    definition = get_node_definition(kind)
    if definition is None:
        return jsonify({"error": "node_kind_not_found", "error_code": "node_kind_not_found"}), 404
    return jsonify(definition), 200


# ── Validate (VPAD-002 + VPDF-002) ───────────────────────────────────────────


@vp_bp.post("/validate")
def validate():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    result = _validator.validate(graph)
    return jsonify(result.as_dict()), 200 if result.valid else 422


# ── Dry-run (VPAD-010) ────────────────────────────────────────────────────────


@vp_bp.post("/dry-run")
@check_user_auth
def dry_run():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400

    validation = _validator.validate(graph)
    annotated = annotate_graph(graph)
    policy = policy_summary(annotated)

    blueprint = None
    if validation.valid:
        blueprint = graph_to_blueprint_dict(annotated)

    executor = get_step_executor()
    step_execution_plan = [p.as_dict() for p in executor.execution_plan(graph.steps)]
    non_executable = [p for p in step_execution_plan if not p["executable"]]
    model_plan = _build_model_plan(graph) if validation.valid else _invalid_model_plan()

    return jsonify(
        {
            "dry_run": True,
            "validation": validation.as_dict(),
            "policy_summary": policy,
            "blueprint": blueprint,
            "step_count": len(graph.steps),
            "edge_count": len(graph.edges),
            "step_execution_plan": step_execution_plan,
            "non_executable_count": len(non_executable),
            **model_plan,
        }
    ), 200


@vp_bp.post("/model-routing/validate")
@check_user_auth
def validate_model_routing():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    validation = _validator.validate(graph)
    model_plan = _build_model_plan(graph) if validation.valid else _invalid_model_plan()
    return jsonify({"validation": validation.as_dict(), **model_plan}), 200 if validation.valid else 422


@vp_bp.post("/model-routing/estimate-cost")
@check_user_auth
def estimate_model_cost():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    validation = _validator.validate(graph)
    if not validation.valid:
        return jsonify(
            {
                "validation": validation.as_dict(),
                **_invalid_model_plan(),
            }
        ), 422
    model_plan = _build_model_plan(graph)
    return jsonify({"validation": validation.as_dict(), **model_plan}), 200


# ── Graph persistence (VPPERS-001) ────────────────────────────────────────────


@vp_bp.post("/graphs")
@check_user_auth
def save_graph():
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    graph = _owned_graph_model(graph, principal)
    expected_revision, expected_hash = _definition_preconditions(graph)
    with Session(engine) as session:
        existing = session.get(VisualProcessGraphDB, graph.id)
        if existing:
            try:
                previous = json.loads(existing.graph_json)
            except (TypeError, ValueError):
                return jsonify({"error": "not_found"}), 404
            authorized, migrated = authorize_graph(previous, principal)
            if not authorized:
                return jsonify({"error": "resource_id_unavailable", "error_code": "resource_id_unavailable"}), 409
            if migrated:
                existing.graph_json = json.dumps(previous)
            _archive_graph_revision(session, existing)
            try:
                write = visual_process_definition_service.replace(
                    session,
                    existing,
                    graph,
                    expected_revision=expected_revision,
                    expected_hash=expected_hash,
                    require_precondition=False,
                )
            except (VisualProcessDefinitionConflict, VisualProcessDefinitionSecurityError) as exc:
                session.rollback()
                return _definition_error(exc)
        else:
            try:
                write = visual_process_definition_service.create(session, graph)
            except VisualProcessDefinitionSecurityError as exc:
                session.rollback()
                return _definition_error(exc)
        session.commit()
    return _definition_save_response(write)


@vp_bp.get("/graphs")
@check_user_auth
def list_graphs():
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with Session(engine) as session:
        rows = session.exec(select(VisualProcessGraphDB)).all()
        visible: list[tuple[VisualProcessGraphDB, dict]] = []
        changed = False
        for row in rows:
            try:
                graph_data = json.loads(row.graph_json)
            except (TypeError, ValueError):
                continue
            authorized, migrated = authorize_graph(graph_data, principal)
            if not authorized:
                continue
            if migrated:
                row.graph_json = json.dumps(graph_data)
                session.add(row)
                changed = True
            visible.append((row, graph_data))
        if changed:
            session.commit()
            for row, _ in visible:
                session.refresh(row)
    rows_sorted = sorted(visible, key=lambda item: item[0].updated_at, reverse=True)
    return jsonify(
        [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "tags": [tag for tag in row.tags.split(",") if tag],
                "updated_at": row.updated_at,
                "created_at": row.created_at,
                "version": str(graph_data.get("version") or "1.0"),
                "graph_schema_version": row.graph_schema_version,
                "node_registry_version": row.node_registry_version,
                "definition_revision": row.definition_revision,
                "base_graph_hash": row.base_graph_hash,
                "origin": "revision" if "@" in row.id else "custom",
            }
            for row, graph_data in rows_sorted
        ]
    ), 200


@vp_bp.get("/graphs/<graph_id>")
@check_user_auth
def load_graph(graph_id: str):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        try:
            data = json.loads(row.graph_json)
        except Exception:
            return jsonify({"error": "corrupt_graph_json"}), 500
        authorized, migrated = authorize_graph(data, principal)
        if not authorized:
            return jsonify({"error": "not_found"}), 404
        if migrated:
            row.graph_json = json.dumps(data)
            session.add(row)
            session.commit()
        graph = VisualProcessGraph.model_validate(data).model_copy(
            update={
                "definition_revision": int(row.definition_revision or 1),
                "base_graph_hash": str(row.base_graph_hash or ""),
                "graph_schema_version": str(row.graph_schema_version or "1"),
                "node_registry_version": str(row.node_registry_version or "1"),
            }
        )
        if not graph.base_graph_hash:
            graph = graph.model_copy(update={"base_graph_hash": graph.definition_hash()})
        payload = graph.model_dump(exclude={"runtime_overlay"})
        if graph.runtime_overlay:
            payload["runtime_overlay"] = graph.runtime_overlay
    return jsonify(public_graph(payload)), 200


@vp_bp.put("/graphs/<graph_id>")
@check_user_auth
def update_graph(graph_id: str):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    if graph.id != graph_id:
        return jsonify({"error": "graph_id_mismatch", "error_code": "graph_id_mismatch"}), 400
    graph = _owned_graph_model(graph, principal)
    expected_revision, expected_hash = _definition_preconditions(graph)
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        try:
            previous = json.loads(row.graph_json)
        except (TypeError, ValueError):
            return jsonify({"error": "not_found"}), 404
        authorized, migrated = authorize_graph(previous, principal)
        if not authorized:
            return jsonify({"error": "not_found"}), 404
        if migrated:
            row.graph_json = json.dumps(previous)
        _archive_graph_revision(session, row)
        try:
            write = visual_process_definition_service.replace(
                session,
                row,
                graph,
                expected_revision=expected_revision,
                expected_hash=expected_hash,
                require_precondition=False,
            )
        except (VisualProcessDefinitionConflict, VisualProcessDefinitionSecurityError) as exc:
            session.rollback()
            return _definition_error(exc)
        session.commit()
    return _definition_save_response(write)


@vp_bp.post("/v2/graphs")
@check_user_auth
def save_graph_v2():
    """Create or replace a definition; replacements require revision and ETag."""

    return _save_graph_v2_impl(None)


@vp_bp.put("/v2/graphs/<graph_id>")
@check_user_auth
def update_graph_v2(graph_id: str):
    return _save_graph_v2_impl(graph_id)


def _save_graph_v2_impl(graph_id: str | None):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    assert graph is not None
    if graph_id is not None and graph.id != graph_id:
        return jsonify({"error": "graph_id_mismatch", "error_code": "graph_id_mismatch"}), 400
    graph = _owned_graph_model(graph, principal)
    expected_revision, expected_hash = _definition_preconditions(graph)
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph.id)
        if row is None:
            if graph_id is not None:
                return jsonify({"error": "not_found", "error_code": "not_found"}), 404
            try:
                write = visual_process_definition_service.create(session, graph)
            except VisualProcessDefinitionSecurityError as exc:
                session.rollback()
                return _definition_error(exc)
        else:
            try:
                previous = json.loads(row.graph_json)
            except (TypeError, ValueError):
                return jsonify({"error": "corrupt_graph_json", "error_code": "corrupt_graph_json"}), 500
            authorized, migrated = authorize_graph(previous, principal)
            if not authorized:
                status = 409 if graph_id is None else 404
                code = "resource_id_unavailable" if graph_id is None else "not_found"
                return jsonify({"error": code, "error_code": code}), status
            if migrated:
                row.graph_json = json.dumps(previous)
            _archive_graph_revision(session, row)
            try:
                write = visual_process_definition_service.replace(
                    session,
                    row,
                    graph,
                    expected_revision=expected_revision,
                    expected_hash=expected_hash,
                    require_precondition=True,
                )
            except (VisualProcessDefinitionConflict, VisualProcessDefinitionSecurityError) as exc:
                session.rollback()
                return _definition_error(exc)
        session.commit()
    response, status = _definition_save_response(write)
    response.headers["ETag"] = f'"{write.base_graph_hash}"'
    return response, status


@vp_bp.delete("/graphs/<graph_id>")
@check_user_auth
def delete_graph(graph_id: str):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        try:
            data = json.loads(row.graph_json)
        except (TypeError, ValueError):
            return jsonify({"error": "not_found"}), 404
        if not authorize_graph(data, principal)[0]:
            return jsonify({"error": "not_found"}), 404
        session.delete(row)
        session.commit()
    return "", 204


@vp_bp.post("/v1/location")
@check_user_auth
def workflow_location():
    """Return deterministic topology facts for one persisted definition/draft."""

    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    graph_id = str(body.get("graph_id") or "")
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if row is None:
            return jsonify({"error": "not_found", "error_code": "not_found"}), 404
        try:
            stored = json.loads(row.graph_json)
        except (TypeError, ValueError):
            return jsonify({"error": "corrupt_graph_json", "error_code": "corrupt_graph_json"}), 500
        if not authorize_graph(stored, principal)[0]:
            return jsonify({"error": "not_found", "error_code": "not_found"}), 404
        authoritative = VisualProcessGraph.model_validate(stored).model_copy(
            update={
                "definition_revision": int(row.definition_revision or 1),
                "base_graph_hash": str(row.base_graph_hash or ""),
                "graph_schema_version": str(row.graph_schema_version or "1"),
                "node_registry_version": str(row.node_registry_version or "1"),
            }
        )
    try:
        draft = (
            VisualProcessGraph.model_validate(body["draft_graph"])
            if isinstance(body.get("draft_graph"), dict)
            else authoritative
        )
        if draft.id != authoritative.id:
            return jsonify({"error": "draft_graph_mismatch", "error_code": "draft_graph_mismatch"}), 409
        if draft.definition_revision != authoritative.definition_revision:
            return jsonify(
                {
                    "error": "definition_revision_conflict",
                    "error_code": "definition_revision_conflict",
                    "expected_revision": authoritative.definition_revision,
                    "actual_revision": draft.definition_revision,
                }
            ), 409
        result = visual_process_location_service.analyze(
            graph=draft,
            location=body.get("location") or {},
            draft_hash=(
                draft.definition_hash()
                if isinstance(body.get("draft_graph"), dict)
                else authoritative.base_graph_hash or authoritative.definition_hash()
            ),
        )
    except Exception as exc:
        return jsonify({"error": "invalid_location_request", "detail": str(exc)[:1000]}), 422
    return jsonify(result.as_dict()), 200


# ── Save as Blueprint (VPBLUEPR-001) ─────────────────────────────────────────


@vp_bp.post("/save-blueprint")
@check_user_auth
def save_blueprint():
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    graph = _owned_graph_model(graph, principal)
    validation = _validator.validate(graph)
    if not validation.valid:
        return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
    annotated = annotate_graph(graph)
    blueprint = graph_to_blueprint_dict(annotated)
    # Store the blueprint in the visual process graphs table using the graph's id
    # as a stable identifier, prefixed to distinguish blueprints from raw graphs.
    bp_id = f"bp-{graph.id}"
    now = time.time()
    row = VisualProcessGraphDB(
        id=bp_id,
        name=f"[Blueprint] {graph.name}",
        description=graph.description,
        tags=",".join(graph.tags),
        graph_json=json.dumps(
            {
                "graph": graph.model_dump(),
                "blueprint": blueprint,
                "metadata": {"owner_principal": principal.to_dict()},
            }
        ),
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        existing = session.get(VisualProcessGraphDB, bp_id)
        if existing:
            try:
                existing_data = json.loads(existing.graph_json)
            except (TypeError, ValueError):
                return jsonify({"error": "not_found"}), 404
            if not authorize_graph(existing_data, principal)[0]:
                return jsonify({"error": "resource_id_unavailable", "error_code": "resource_id_unavailable"}), 409
            existing.graph_json = row.graph_json
            existing.updated_at = now
            session.add(existing)
        else:
            session.add(row)
        session.commit()
    return jsonify({"blueprint_id": bp_id, "saved": True}), 200


# ── BPMN import/export ───────────────────────────────────────────────────────


@vp_bp.post("/bpmn/import")
def bpmn_import():
    body = request.get_json(silent=True) or {}
    xml = str(body.get("bpmn_xml") or body.get("xml") or "").strip()
    if not xml:
        return jsonify({"error": "bpmn_xml_required"}), 400
    try:
        result = import_bpmn_xml(xml)
    except ValueError as exc:
        return jsonify({"error": "invalid_bpmn", "detail": str(exc)}), 400
    validation = _validator.validate(result.graph) if result.graph else None
    return jsonify(
        {
            "graph": result.graph.model_dump() if result.graph else None,
            "warnings": result.warnings,
            "validation": validation.as_dict() if validation else None,
        }
    ), 200 if validation is None or validation.valid else 422


@vp_bp.post("/bpmn/export")
def bpmn_export():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    validation = _validator.validate(graph)
    if not validation.valid:
        return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
    result = export_bpmn_xml(graph)
    return jsonify({"bpmn_xml": result.bpmn_xml, "warnings": result.warnings}), 200


# ── Canonical workflow request / backend port ────────────────────────────────


@vp_bp.post("/workflow-request")
@check_strict_auth
def workflow_request():
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_REQUEST_BYTES)
    if body_error is not None:
        return body_error
    assert body is not None
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    validation = _validator.validate(graph)
    if not validation.valid:
        return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
    workflow = _compile_workflow_request(graph, body)
    errors = workflow.validate()
    return jsonify(
        {
            "workflow_request": workflow.to_dict(),
            "validation": validation.as_dict(),
            "errors": errors,
        }
    ), 200 if not errors else 422


@vp_bp.post("/workflow/start")
@check_strict_auth
def workflow_start():
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_REQUEST_BYTES)
    if body_error is not None:
        return body_error
    assert body is not None
    if "workflow_request" in body:
        try:
            workflow = WorkflowRequest.from_mapping(body.get("workflow_request") or {})
        except Exception as exc:
            return jsonify({"error": "invalid_workflow_request", "detail": str(exc)}), 400
        errors = workflow.validate()
        if errors:
            return jsonify({"error": "invalid_workflow_request", "errors": errors}), 422
        # Canonical edge identity is Hub-derived from a validated graph. A
        # direct neutral WorkflowRequest may not assert that internal catalog.
        direct_metadata = dict(workflow.metadata)
        direct_metadata.pop(CASEFLOW_EDGE_CATALOG_METADATA_KEY, None)
        workflow = replace(workflow, metadata=direct_metadata)
    else:
        graph, err = _parse_graph()
        if err:
            return jsonify(err), 400
        validation = _validator.validate(graph)
        if not validation.valid:
            return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
        workflow = _compile_workflow_request(graph, body)
    invalid_id = validate_workflow_id(workflow.workflow_id)
    if invalid_id is not None:
        return invalid_id
    try:
        principal = workflow_principal()
    except ValueError:
        return api_response(
            status="error",
            message="authenticated workflow principal required",
            data={"reason_code": "workflow_principal_required"},
            code=401,
        )
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    reservation = workflow_route_authorization_service.reserve(workflow.workflow_id, principal)
    if reservation in {"foreign", "duplicate"}:
        return api_response(
            status="error",
            message="workflow id unavailable",
            data={"reason_code": "workflow_id_unavailable"},
            code=409,
        )
    if reservation != "reserved":
        return backend_error("workflow_id_invalid", code=400)

    workflow = replace(
        workflow,
        requested_by=principal.subject,
        metadata={
            **dict(workflow.metadata),
            "authorization_scope": {
                "tenant_id": principal.tenant_id,
                "subject": principal.subject,
            },
        },
    )
    try:
        status = backend.start_workflow(workflow)
    except Exception as exc:  # noqa: BLE001
        workflow_route_authorization_service.release(workflow.workflow_id, principal)
        log_audit(
            "workflow_backend_start_failed",
            {"workflow_id": workflow.workflow_id, "exception_type": type(exc).__name__},
        )
        return backend_error("workflow_backend_unavailable", code=503)
    if str(status.get("status") or "").lower() in {"failed", "degraded", "unavailable"}:
        workflow_route_authorization_service.release(workflow.workflow_id, principal)
    return backend_result(status)


@vp_bp.get("/workflow/<workflow_id>/status")
@check_strict_auth
def workflow_status(workflow_id: str):
    principal, auth_error = require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    assert principal is not None
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.get_workflow_status(workflow_id)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_status_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        return backend_error("workflow_backend_unavailable", code=503)
    if str(status.get("status") or "").lower() == "not_found" and principal is not None:
        workflow_route_authorization_service.release(workflow_id, principal)
    return backend_result(status)


@vp_bp.post("/workflow/<workflow_id>/cancel")
@check_strict_auth
def workflow_cancel(workflow_id: str):
    principal, auth_error = require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_CANCEL_BYTES, required=False)
    if body_error is not None:
        return body_error
    assert body is not None
    reason = str(body.get("reason") or "").strip()
    if len(reason) > 1000:
        return api_response(
            status="error",
            message="cancel reason too long",
            data={"reason_code": "workflow_cancel_reason_too_long"},
            code=422,
        )
    assert principal is not None
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.cancel_workflow(workflow_id, reason=reason)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_cancel_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        return backend_error("workflow_backend_unavailable", code=503)
    return backend_result(status)


@vp_bp.post("/workflow/<workflow_id>/signal")
@check_strict_auth
def workflow_signal(workflow_id: str):
    principal, auth_error = require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_SIGNAL_BYTES)
    if body_error is not None:
        return body_error
    assert body is not None and principal is not None
    if not isinstance(body.get("payload", {}), dict):
        return api_response(
            status="error",
            message="workflow signal payload must be an object",
            data={"reason_code": "workflow_signal_payload_invalid"},
            code=422,
        )
    signal = WorkflowSignal.from_mapping(
        {
            **body,
            "payload": redact_json(
                redact(dict(body.get("payload") or {}), VisibilityLevel.PUBLIC)
            ),
            "actor": principal.subject,
        }
    )
    if not signal.name:
        return api_response(
            status="error",
            message="signal name required",
            data={"reason_code": "workflow_signal_name_required"},
            code=400,
        )
    if len(signal.name) > 64 or not all(character.isalnum() or character in "._-" for character in signal.name):
        return api_response(
            status="error",
            message="invalid signal name",
            data={"reason_code": "workflow_signal_name_invalid"},
            code=422,
        )
    return _dispatch_workflow_signal(workflow_id, principal, signal)


@vp_bp.post("/workflow/<workflow_id>/resume")
@check_strict_auth
def workflow_resume(workflow_id: str):
    return _named_workflow_control(workflow_id, "resume")


@vp_bp.post("/workflow/<workflow_id>/retry")
@check_strict_auth
def workflow_retry(workflow_id: str):
    return _named_workflow_control(workflow_id, "retry")


def _named_workflow_control(workflow_id: str, command_name: str):
    principal, auth_error = require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_SIGNAL_BYTES, required=False)
    if body_error is not None:
        return body_error
    assert body is not None and principal is not None
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        return api_response(
            status="error",
            message="workflow control payload must be an object",
            data={"reason_code": "workflow_signal_payload_invalid"},
            code=422,
        )
    signal = WorkflowSignal(
        name=command_name,
        payload=dict(redact_json(redact(payload, VisibilityLevel.PUBLIC)) or {}),
        actor=principal.subject,
    )
    return _dispatch_workflow_signal(workflow_id, principal, signal)


def _dispatch_workflow_signal(workflow_id: str, principal, signal: WorkflowSignal):
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.signal_workflow(workflow_id, signal)
    except PermissionError as exc:
        reason_code = str(exc)
        safe_reason = (
            reason_code
            if reason_code in {
                "temporal_hub_verified_command_required",
                "workflow_control_checkpoint_binding_mismatch",
                "workflow_control_plan_binding_mismatch",
                "workflow_control_policy_binding_mismatch",
                "workflow_control_principal_binding_mismatch",
                "workflow_control_run_binding_mismatch",
            }
            else "workflow_control_command_denied"
        )
        log_audit(
            "workflow_control_command_denied",
            {"workflow_id": workflow_id, "reason_code": safe_reason},
        )
        return backend_error(safe_reason, code=409)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_signal_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        return backend_error("workflow_backend_unavailable", code=503)
    return backend_result(status)


@vp_bp.get("/workflow/<workflow_id>/events")
@check_strict_auth
def workflow_events(workflow_id: str):
    principal, auth_error = require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    assert principal is not None
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.get_workflow_status(workflow_id)
        if str(status.get("status") or "").lower() in {"degraded", "unavailable", "not_found"}:
            return backend_result(status)
        events = backend.list_workflow_events(workflow_id)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_events_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        return backend_error("workflow_backend_unavailable", code=503)
    safe_events = [dict(redact(event, VisibilityLevel.USER) or {}) for event in events if isinstance(event, dict)]
    return jsonify({"events": safe_events}), 200


@vp_bp.post("/workflow/<workflow_id>/caseflow-edge-trace")
@check_strict_auth
def caseflow_edge_trace(workflow_id: str):
    """Project bounded directional edge evidence from existing Hub history."""

    if request.args:
        return api_response(
            status="error",
            message="caseflow edge trace parameters must not be sent in a URL",
            data={"reason_code": "caseflow_edge_trace_query_transport_forbidden"},
            code=400,
        )
    principal, auth_error = require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(
        max_bytes=MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES
    )
    if body_error is not None:
        return body_error
    try:
        query = CaseflowEdgeTraceQuery.from_mapping(body or {})
    except CaseflowEdgeTraceProjectionError as exc:
        return api_response(
            status="error",
            message="invalid caseflow edge trace request",
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    assert principal is not None
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        projection = (
            get_caseflow_agent_collaboration_trace_projection_service().read(
                principal=principal,
                workflow_id=workflow_id,
                run_id=query.run_id,
                history=backend,
            )
        )
    except CaseflowEdgeTraceProjectionError as exc:
        return api_response(
            status="error",
            message=(
                "workflow not found"
                if exc.status_code == 404
                else "caseflow edge trace unavailable"
            ),
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "caseflow_edge_trace_projection_failed",
            {
                "workflow_id": workflow_id,
                "exception_type": type(exc).__name__,
            },
        )
        return backend_error("caseflow_edge_trace_unavailable", code=503)
    return jsonify(projection), 200


@vp_bp.post("/workflow/events/stream")
@check_strict_auth
def workflow_event_stream():
    """Return a bounded, cursor-resumable NDJSON page from the Hub stream."""

    if request.args:
        return api_response(
            status="error",
            message="workflow stream parameters must not be sent in a URL",
            data={"reason_code": "workflow_stream_query_transport_forbidden"},
            code=400,
        )
    body, body_error = workflow_json_body(max_bytes=8 * 1024)
    if body_error is not None:
        return body_error
    try:
        stream_request = WorkflowStreamRequest.from_mapping(body or {})
    except WorkflowStreamError as exc:
        return api_response(
            status="error",
            message="invalid workflow stream request",
            data={"reason_code": exc.reason_code},
            code=422,
        )
    principal, auth_error = require_workflow_owner(stream_request.workflow_id)
    if auth_error is not None:
        return auth_error
    assert principal is not None
    backend, backend_failure = configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.get_workflow_status(stream_request.workflow_id)
        if str(status.get("status") or "").lower() in {"degraded", "unavailable", "not_found"}:
            return backend_result(status)
        batch = WorkflowStreamService(backend).read(stream_request)
    except WorkflowStreamError as exc:
        return api_response(
            status="error",
            message="workflow stream cursor rejected",
            data={"reason_code": exc.reason_code},
            code=409,
        )
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_stream_failed",
            {
                "workflow_id": stream_request.workflow_id,
                "exception_type": type(exc).__name__,
            },
        )
        return backend_error("workflow_stream_unavailable", code=503)

    log_audit(
        "workflow_stream_opened",
        {
            "workflow_id": stream_request.workflow_id,
            "after_cursor": stream_request.after_cursor,
            "frame_count": len(batch.frames),
        },
    )

    @stream_with_context
    def generate():
        try:
            for frame in batch.frames:
                yield (
                    json.dumps(
                        frame.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    + "\n"
                )
        finally:
            # A disconnected client reaches this path as GeneratorExit; the
            # cursor makes reconnect safe and no worker execution is affected.
            log_audit(
                "workflow_stream_closed",
                {
                    "workflow_id": stream_request.workflow_id,
                    "next_cursor": batch.next_cursor,
                },
            )

    response = Response(generate(), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Workflow-Next-Cursor"] = batch.next_cursor
    response.headers["X-Workflow-Has-More"] = "true" if batch.has_more else "false"
    return response


# ── Mermaid (VPAD-009) ────────────────────────────────────────────────────────


@vp_bp.post("/mermaid")
def mermaid():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    body = request.get_json(silent=True) or {}
    direction = body.get("direction") or "LR"
    include_tui = bool(body.get("include_tui", False))
    result = {"mermaid": to_mermaid(graph, direction=direction)}
    if include_tui:
        result["tui"] = to_tui_text(graph)
    return jsonify(result), 200


# ── Policy summary (VPAD-008) ─────────────────────────────────────────────────


@vp_bp.post("/policy-summary")
def policy_summary_route():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    annotated = annotate_graph(graph)
    summary = policy_summary(annotated)
    per_step = {s.id: s.policy_hints for s in annotated.steps}
    return jsonify({"summary": summary, "per_step": per_step}), 200


# ── Context assembly (VPDF-003) ───────────────────────────────────────────────


@vp_bp.post("/assemble-context")
def assemble_context():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    body = request.get_json(silent=True) or {}
    step_id = body.get("step_id") or ""
    runtime_artifacts = body.get("runtime_artifacts") or {}
    if not step_id:
        return jsonify({"error": "step_id_required"}), 400
    reg = get_skill_profile_registry()
    profiles = {p.id: p.as_dict() for p in reg.all()}
    assembler = StepContextAssembler(graph, skill_profiles=profiles)
    try:
        ctx = assembler.assemble(step_id, runtime_artifacts=runtime_artifacts)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(ctx.as_dict()), 200
