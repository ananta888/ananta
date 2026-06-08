"""Visual Process Designer API (VPAD-009 TUI, VPAD-010 dry-run, VPDF-004 artifacts).

GET  /api/visual-process/presets            — list presets
GET  /api/visual-process/presets/<id>       — get preset graph
GET  /api/visual-process/skill-profiles     — agent library (VPAD-005)
POST /api/visual-process/validate           — validate a graph (VPAD-002 + VPDF-002)
POST /api/visual-process/classify-step      — classify a single step
POST /api/visual-process/dry-run            — validate + blueprint mapping (VPAD-010)
POST /api/visual-process/mermaid            — Mermaid export (VPAD-009)
POST /api/visual-process/policy-summary     — policy/security summary (VPAD-008)
POST /api/visual-process/assemble-context   — context for one step (VPDF-003)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict
from agent.visual_process.context_assembly import StepContextAssembler
from agent.visual_process.mermaid_export import to_mermaid, to_tui_text
from agent.visual_process.models import VisualProcessGraph
from agent.visual_process.policy_hints import annotate_graph, policy_summary
from agent.visual_process.presets import get_preset, list_presets
from agent.visual_process.skill_profiles import get_skill_profile_registry
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
