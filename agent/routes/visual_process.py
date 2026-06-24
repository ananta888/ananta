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
POST /api/visual-process/save-blueprint         — save dry-run result as Blueprint (VPBLUEPR-001)

-- Graph persistence (VPPERS-001) --
POST   /api/visual-process/graphs               — save new graph
GET    /api/visual-process/graphs               — list saved graphs
GET    /api/visual-process/graphs/<id>          — load graph
PUT    /api/visual-process/graphs/<id>          — update graph
DELETE /api/visual-process/graphs/<id>          — delete graph
"""
from __future__ import annotations

import json
import time

from flask import Blueprint, jsonify, request
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.workflow_backend import WorkflowRequest, WorkflowSignal
from agent.services.workflow_backend_factory import get_workflow_backend
from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict, graph_to_workflow_request
from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml
from agent.visual_process.context_assembly import StepContextAssembler
from agent.visual_process.mermaid_export import to_mermaid, to_tui_text
from agent.visual_process.models import VisualProcessGraph
from agent.visual_process.policy_hints import annotate_graph, policy_summary
from agent.visual_process.presets import get_preset, list_presets
from agent.visual_process.skill_profiles import get_skill_profile_registry
from agent.visual_process.task_kind_registry import list_task_kinds
from agent.visual_process.validator import VisualProcessValidator

vp_bp = Blueprint("visual_process", __name__, url_prefix="/api/visual-process")
_validator = VisualProcessValidator()


def _parse_graph() -> tuple[VisualProcessGraph | None, dict | None]:
    body = request.get_json(silent=True) or {}
    graph_data = body.get("graph") or body
    try:
        return VisualProcessGraph.model_validate(graph_data), None
    except Exception as exc:
        return None, {"error": "invalid_graph", "detail": str(exc)}


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
def skill_profiles():
    reg = get_skill_profile_registry()
    return jsonify(reg.as_library()), 200


@vp_bp.get("/skill-profiles/<profile_id>")
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

    return jsonify({
        "dry_run": True,
        "validation": validation.as_dict(),
        "policy_summary": policy,
        "blueprint": blueprint,
        "step_count": len(graph.steps),
        "edge_count": len(graph.edges),
    }), 200


# ── Graph persistence (VPPERS-001) ────────────────────────────────────────────

@vp_bp.post("/graphs")
def save_graph():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    now = time.time()
    row = VisualProcessGraphDB(
        id=graph.id,
        name=graph.name,
        description=graph.description,
        tags=",".join(graph.tags),
        graph_json=json.dumps(graph.model_dump()),
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        existing = session.get(VisualProcessGraphDB, graph.id)
        if existing:
            existing.name = row.name
            existing.description = row.description
            existing.tags = row.tags
            existing.graph_json = row.graph_json
            existing.updated_at = now
            session.add(existing)
        else:
            session.add(row)
        session.commit()
    return jsonify({"id": graph.id, "saved": True}), 200


@vp_bp.get("/graphs")
def list_graphs():
    with Session(engine) as session:
        rows = session.exec(select(VisualProcessGraphDB)).all()
    rows_sorted = sorted(rows, key=lambda r: r.updated_at, reverse=True)
    return jsonify([
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "tags": [t for t in r.tags.split(",") if t],
            "updated_at": r.updated_at,
            "created_at": r.created_at,
        }
        for r in rows_sorted
    ]), 200


@vp_bp.get("/graphs/<graph_id>")
def load_graph(graph_id: str):
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
    if not row:
        return jsonify({"error": "not_found"}), 404
    try:
        data = json.loads(row.graph_json)
    except Exception:
        return jsonify({"error": "corrupt_graph_json"}), 500
    return jsonify(data), 200


@vp_bp.put("/graphs/<graph_id>")
def update_graph(graph_id: str):
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        row.name = graph.name
        row.description = graph.description
        row.tags = ",".join(graph.tags)
        row.graph_json = json.dumps(graph.model_dump())
        row.updated_at = time.time()
        session.add(row)
        session.commit()
    return jsonify({"id": graph_id, "saved": True}), 200


@vp_bp.delete("/graphs/<graph_id>")
def delete_graph(graph_id: str):
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        session.delete(row)
        session.commit()
    return "", 204


# ── Save as Blueprint (VPBLUEPR-001) ─────────────────────────────────────────

@vp_bp.post("/save-blueprint")
def save_blueprint():
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
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
        graph_json=json.dumps({"graph": graph.model_dump(), "blueprint": blueprint}),
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        existing = session.get(VisualProcessGraphDB, bp_id)
        if existing:
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
    return jsonify({
        "graph": result.graph.model_dump() if result.graph else None,
        "warnings": result.warnings,
        "validation": validation.as_dict() if validation else None,
    }), 200 if validation is None or validation.valid else 422


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
def workflow_request():
    body = request.get_json(silent=True) or {}
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    validation = _validator.validate(graph)
    if not validation.valid:
        return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
    workflow = _compile_workflow_request(graph, body)
    errors = workflow.validate()
    return jsonify({
        "workflow_request": workflow.to_dict(),
        "validation": validation.as_dict(),
        "errors": errors,
    }), 200 if not errors else 422


@vp_bp.post("/workflow/start")
def workflow_start():
    body = request.get_json(silent=True) or {}
    if "workflow_request" in body:
        try:
            workflow = WorkflowRequest.from_mapping(body.get("workflow_request") or {})
        except Exception as exc:
            return jsonify({"error": "invalid_workflow_request", "detail": str(exc)}), 400
        errors = workflow.validate()
        if errors:
            return jsonify({"error": "invalid_workflow_request", "errors": errors}), 422
    else:
        graph, err = _parse_graph()
        if err:
            return jsonify(err), 400
        validation = _validator.validate(graph)
        if not validation.valid:
            return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
        workflow = _compile_workflow_request(graph, body)
    status = get_workflow_backend().start_workflow(workflow)
    return jsonify(status), 200 if status.get("status") != "failed" else 422


@vp_bp.get("/workflow/<workflow_id>/status")
def workflow_status(workflow_id: str):
    status = get_workflow_backend().get_workflow_status(workflow_id)
    return jsonify(status), 200 if status.get("status") != "not_found" else 404


@vp_bp.post("/workflow/<workflow_id>/cancel")
def workflow_cancel(workflow_id: str):
    body = request.get_json(silent=True) or {}
    status = get_workflow_backend().cancel_workflow(workflow_id, reason=str(body.get("reason") or ""))
    return jsonify(status), 200 if status.get("status") != "not_found" else 404


@vp_bp.post("/workflow/<workflow_id>/signal")
def workflow_signal(workflow_id: str):
    body = request.get_json(silent=True) or {}
    signal = WorkflowSignal.from_mapping(body)
    if not signal.name:
        return jsonify({"error": "signal_name_required"}), 400
    status = get_workflow_backend().signal_workflow(workflow_id, signal)
    return jsonify(status), 200 if status.get("status") != "not_found" else 404


@vp_bp.get("/workflow/<workflow_id>/events")
def workflow_events(workflow_id: str):
    return jsonify({"events": get_workflow_backend().list_workflow_events(workflow_id)}), 200


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
