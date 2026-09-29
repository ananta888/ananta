"""Graph design endpoints: validation, dry-run, model routing, location, blueprint save,
BPMN import/export and Mermaid/policy/context exports.
"""

from __future__ import annotations

import json
import time

from flask import (
    jsonify,
    request,
)
from sqlmodel import Session

from agent.auth import (
    check_strict_auth,
    check_user_auth,
)
from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.routes.visual_process_blueprint import vp_bp
from agent.routes.visual_process_graph_support import (
    _graph_principal,
    _owned_graph_model,
    _parse_graph,
    _validator,
)
from agent.routes.visual_process_model_plan import (
    _build_model_plan,
    _invalid_model_plan,
)
from agent.services.chat_process_binding import authorize_graph
from agent.services.visual_process_location_service import visual_process_location_service
from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict
from agent.visual_process.bpmn_adapter import (
    export_bpmn_xml,
    import_bpmn_xml,
)
from agent.visual_process.context_assembly import StepContextAssembler
from agent.visual_process.mermaid_export import (
    to_mermaid,
    to_tui_text,
)
from agent.visual_process.models import VisualProcessGraph
from agent.visual_process.policy_hints import (
    annotate_graph,
    policy_summary,
)
from agent.visual_process.skill_profiles import get_skill_profile_registry
from agent.visual_process.step_executor import get_step_executor

from .workflow_control_security import (
    MAX_WORKFLOW_REQUEST_BYTES,
    workflow_json_body,
)

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
    blueprint_issues = []
    if validation.valid:
        from agent.visual_process.bpmn_execution_support import BpmnExecutionError

        try:
            blueprint = graph_to_blueprint_dict(annotated)
        except BpmnExecutionError as exc:
            blueprint_issues = exc.as_dict()["issues"]

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
            "blueprint_issues": blueprint_issues,
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
    from agent.visual_process.bpmn_execution_support import BpmnExecutionError

    try:
        blueprint = graph_to_blueprint_dict(annotated)
    except BpmnExecutionError as exc:
        return jsonify(exc.as_dict()), 422
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


@vp_bp.get("/bpmn/capabilities")
@check_strict_auth
def bpmn_capabilities():
    from agent.visual_process.bpmn_execution_support import capability_catalog

    return jsonify(capability_catalog())


@vp_bp.post("/bpmn/import")
def bpmn_import():
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_REQUEST_BYTES)
    if body_error is not None:
        return body_error
    xml = str(body.get("bpmn_xml") or body.get("xml") or "").strip()
    if not xml:
        return jsonify({"error": "bpmn_xml_required"}), 400
    try:
        result = import_bpmn_xml(xml)
    except ValueError as exc:
        return jsonify({"error": "invalid_bpmn", "detail": str(exc)}), 400
    validation = _validator.validate(result.graph) if result.graph else None
    from agent.visual_process.bpmn_execution_compiler import compile_bpmn_graph
    from agent.visual_process.bpmn_execution_support import BpmnExecutionError

    support = dict(result.graph.metadata["bpmn_execution_report"]) if result.graph else None
    if support is not None:
        support["stage"] = "execution_compile"
        try:
            projection = compile_bpmn_graph(result.graph)
            support["definition_hash"] = projection["definition_hash"]
        except BpmnExecutionError as exc:
            support["supported"] = False
            support["issues"] = exc.as_dict()["issues"]
    return jsonify(
        {
            "graph": result.graph.model_dump() if result.graph else None,
            "warnings": result.warnings,
            "execution_support": support,
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
    from agent.visual_process.bpmn_execution_support import BpmnExecutionError

    try:
        result = export_bpmn_xml(graph)
    except BpmnExecutionError as exc:
        return jsonify(exc.as_dict()), 422
    return jsonify({"bpmn_xml": result.bpmn_xml, "warnings": result.warnings}), 200


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
