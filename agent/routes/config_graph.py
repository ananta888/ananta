"""VACGE-004: REST API for the Config Graph Editor.

Endpoints
---------
GET  /api/config-graph
    Build and return the full graph snapshot.

POST /api/config-graph/effective
    Resolve effective config for {surface, task_kind, path}.

POST /api/config-graph/validate-patch
    Validate a list of patch ops — returns risk tier + errors.

POST /api/config-graph/apply-patch
    Apply a validated patch (requires approval token for HIGH/CRITICAL).
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from agent.services.config_graph_builder_service import get_config_graph_builder_service
from agent.services.config_graph_effective_resolver import EffectiveConfigResolver
from agent.services.config_graph_patch_service import ConfigGraphPatchService, PatchOp

config_graph_bp = Blueprint("config_graph", __name__, url_prefix="/api/config-graph")


def _get_user_config() -> dict:
    try:
        from agent.services.user_config_service import get_user_config_service
        svc = get_user_config_service()
        return dict(svc.config or {})
    except Exception:
        return {}


@config_graph_bp.get("")
def get_config_graph():
    """Build and return the full Ananta configuration graph."""
    cfg = _get_user_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()
    return jsonify(graph.to_dict())


@config_graph_bp.post("/effective")
def get_effective_config():
    """Resolve effective config for a concrete (surface, task_kind, path) tuple."""
    body = request.get_json(force=True, silent=True) or {}
    surface = str(body.get("surface") or "")
    task_kind = str(body.get("task_kind") or "") or None
    path = str(body.get("path") or "") or None

    if not surface:
        return jsonify({"error": "surface is required"}), 400

    cfg = _get_user_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()
    resolver = EffectiveConfigResolver(graph)
    effective = resolver.resolve(surface=surface, task_kind=task_kind, path=path)
    return jsonify(effective.to_dict())


@config_graph_bp.post("/validate-patch")
def validate_patch():
    """Validate patch ops without applying them."""
    body = request.get_json(force=True, silent=True) or {}
    raw_ops = list(body.get("ops") or [])
    if not raw_ops:
        return jsonify({"error": "ops list is required"}), 400

    ops = _parse_ops(raw_ops)
    if ops is None:
        return jsonify({"error": "Invalid op format — each op requires op, target"}), 400

    cfg = _get_user_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()

    patch_svc = ConfigGraphPatchService()
    result = patch_svc.validate(graph, ops)
    return jsonify(result.to_dict())


@config_graph_bp.post("/apply-patch")
def apply_patch():
    """Apply validated patch ops to the live config graph.

    For HIGH/CRITICAL patches an ``approval_token`` must be provided.
    The patched graph is returned so the frontend can refresh immediately.
    """
    body = request.get_json(force=True, silent=True) or {}
    raw_ops = list(body.get("ops") or [])
    approval_token = str(body.get("approval_token") or "")

    if not raw_ops:
        return jsonify({"error": "ops list is required"}), 400

    ops = _parse_ops(raw_ops)
    if ops is None:
        return jsonify({"error": "Invalid op format — each op requires op, target"}), 400

    cfg = _get_user_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()

    patch_svc = ConfigGraphPatchService()
    val = patch_svc.validate(graph, ops)

    if not val.valid:
        return jsonify({"error": "Patch validation failed", "details": val.to_dict()}), 422

    if val.requires_approval:
        result = patch_svc.apply_approved(graph, ops, approval_token)
    else:
        result = patch_svc.apply(graph, ops, skip_validation=True)

    if not result.success:
        return jsonify({"error": "Patch apply failed", "details": result.to_dict()}), 422

    return jsonify({
        "result": result.to_dict(),
        "graph": graph.to_dict(),
    })


def _parse_ops(raw: list) -> list[PatchOp] | None:
    ops: list[PatchOp] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        op = str(item.get("op") or "")
        target = str(item.get("target") or "")
        if not op or not target:
            return None
        ops.append(PatchOp(op=op, target=target, data=dict(item.get("data") or {})))
    return ops
