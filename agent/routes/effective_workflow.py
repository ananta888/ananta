"""Effective Workflow Explorer API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from agent.services.config_graph_builder_service import get_config_graph_builder_service
from agent.services.effective_workflow_resolver import EffectiveWorkflowResolver

effective_workflow_bp = Blueprint(
    "effective_workflow",
    __name__,
    url_prefix="/api/effective-workflow",
)


def _repo_root() -> Path:
    return Path(__file__).parents[2]


def _read_user_json_config() -> dict[str, Any]:
    path = _repo_root() / "user.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_blueprints() -> list[dict[str, Any]]:
    try:
        from agent.routes.blueprint_routes import _serialize_blueprint
        from agent.services.blueprint_seed_service import ensure_seed_blueprints
        from agent.services.repository_registry import get_repository_registry

        ensure_seed_blueprints()
        repos = get_repository_registry()
        return [
            _serialize_blueprint(blueprint)
            for blueprint in repos.team_blueprint_repo.get_all()
        ]
    except Exception:
        return []


def _load_templates() -> list[dict[str, Any]]:
    try:
        from agent.services.repository_registry import get_repository_registry

        return [
            template.model_dump()
            for template in get_repository_registry().template_repo.get_all()
        ]
    except Exception:
        return []


def _build_graph_and_context() -> tuple[Any, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    cfg = _read_user_json_config()
    graph = get_config_graph_builder_service(user_config=cfg).build()
    return graph, cfg, _load_blueprints(), _load_templates()


def _resolve_from_body(body: dict[str, Any]) -> dict[str, Any]:
    graph, cfg, blueprints, templates = _build_graph_and_context()
    return EffectiveWorkflowResolver().resolve(
        graph=graph,
        user_config=cfg,
        surface=str(body.get("surface") or "").strip(),
        path=str(body.get("path") or "").strip() or None,
        task_kind=str(body.get("task_kind") or "").strip() or None,
        blueprints=blueprints,
        templates=templates,
        include_readonly=bool(body.get("include_readonly", True)),
        include_diagnostics=bool(body.get("include_diagnostics", True)),
        include_alternatives=bool(body.get("include_alternatives", True)),
    )


@effective_workflow_bp.get("/options")
def effective_workflow_options():
    graph, cfg, blueprints, _templates = _build_graph_and_context()
    return jsonify(EffectiveWorkflowResolver().options(
        graph=graph,
        user_config=cfg,
        blueprints=blueprints,
    ))


@effective_workflow_bp.post("/resolve")
def resolve_effective_workflow():
    body = request.get_json(force=True, silent=True) or {}
    if not str(body.get("surface") or "").strip():
        return jsonify({"error": "surface is required"}), 400
    result = _resolve_from_body(body)
    return jsonify(result)


@effective_workflow_bp.post("/compare")
def compare_effective_workflow():
    body = request.get_json(force=True, silent=True) or {}
    left = body.get("left") if isinstance(body.get("left"), dict) else {}
    right = body.get("right") if isinstance(body.get("right"), dict) else {}
    if not str(left.get("surface") or "").strip() or not str(right.get("surface") or "").strip():
        return jsonify({"error": "left.surface and right.surface are required"}), 400
    left_result = _resolve_from_body(left)
    right_result = _resolve_from_body(right)
    return jsonify(EffectiveWorkflowResolver().compare(left_result, right_result))


@effective_workflow_bp.post("/explain-node")
def explain_effective_workflow_node():
    body = request.get_json(force=True, silent=True) or {}
    node_id = str(body.get("node_id") or "").strip()
    request_body = body.get("request") if isinstance(body.get("request"), dict) else body
    if not node_id:
        return jsonify({"error": "node_id is required"}), 400
    if not str(request_body.get("surface") or "").strip():
        return jsonify({"error": "request.surface is required"}), 400
    result = _resolve_from_body(request_body)
    node = (result.get("graph") or {}).get("nodes", {}).get(node_id)
    if not node:
        return jsonify({"error": "node not found", "node_id": node_id}), 404
    return jsonify({
        "schema": "ananta.effective_workflow.node_explanation.v1",
        "request": result.get("request"),
        "node": node,
        "incoming_edges": [
            edge for edge in (result.get("graph") or {}).get("edges", [])
            if edge.get("target") == node_id
        ],
        "outgoing_edges": [
            edge for edge in (result.get("graph") or {}).get("edges", [])
            if edge.get("source") == node_id
        ],
        "source_index": [
            item for item in result.get("source_index", [])
            if item.get("node_id") == node_id
        ],
    })
