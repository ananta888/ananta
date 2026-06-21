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

import json
import difflib
from pathlib import Path

from flask import Blueprint, jsonify, request

from agent.common.audit import log_audit
from agent.services.config_graph_approval_service import ConfigGraphApprovalService
from agent.services.config_graph_builder_service import get_config_graph_builder_service
from agent.services.config_graph_effective_resolver import EffectiveConfigResolver
from agent.services.config_graph_patch_service import ConfigGraphPatchService, PatchOp
from agent.services.config_graph_persistence_service import ConfigGraphPersistenceService
from agent.services.hub_worker_graph_service import HubWorkerGraphService

config_graph_bp = Blueprint("config_graph", __name__, url_prefix="/api/config-graph")


def _get_user_config() -> dict:
    try:
        from agent.services.user_config_service import get_user_config_service
        svc = get_user_config_service()
        return dict(svc.config or {})
    except Exception:
        return {}


def _get_repo_root() -> Path:
    return Path(__file__).parents[2]


def _read_user_json_config() -> dict:
    path = _get_repo_root() / "user.json"
    if not path.exists():
        return _get_user_config()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _get_user_config()
    return payload if isinstance(payload, dict) else _get_user_config()


@config_graph_bp.get("")
def get_config_graph():
    """Build and return the full Ananta configuration graph."""
    cfg = _read_user_json_config()
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

    cfg = _read_user_json_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()
    resolver = EffectiveConfigResolver(graph)
    effective = resolver.resolve(surface=surface, task_kind=task_kind, path=path)
    return jsonify(effective.to_dict())


@config_graph_bp.get("/hub-worker")
def get_hub_worker_graph():
    """Return the hub-centered worker orchestration read model."""
    path = str(request.args.get("path") or "").strip() or None
    cfg = _read_user_json_config()
    graph = HubWorkerGraphService().build(user_config=cfg, path=path)
    return jsonify(graph)


@config_graph_bp.get("/restricted-inference/status")
def get_restricted_inference_status():
    """Return restricted inference config, adapter and diagnostic status."""
    cfg = _read_user_json_config()
    from agent.services.model_inference_adapter_registry import get_model_inference_adapter_registry
    from agent.services.restricted_inference_config_service import RestrictedInferenceConfigService

    config_service = RestrictedInferenceConfigService(global_config=cfg)
    restricted_cfg = config_service.resolve()
    registry = get_model_inference_adapter_registry()
    statuses = registry.statuses(restricted_cfg.models)
    dependency_status = {
        status.engine: status.status
        for status in statuses
    }
    diagnostics = config_service.diagnostics(dependency_status=dependency_status)
    return jsonify({
        "adapters": [
            {
                "name": status.name,
                "engine": status.engine,
                "status": status.status,
                "capabilities": sorted(status.capabilities),
                "model_id": status.model_id,
                "device": status.device,
                "revision": status.revision,
                "error": status.error,
            }
            for status in statuses
        ],
        "engines": registry.engines(),
        "capabilities": registry.capabilities(),
        "models": [model.as_dict(redact_secrets=True) for model in restricted_cfg.models],
        "diagnostics": [item.as_dict() for item in diagnostics],
        "config_hash": restricted_cfg.config_hash(),
    })


@config_graph_bp.post("/instruction-layer/diff")
def diff_instruction_layer():
    """Return a review diff for AGENTS.md-style instruction edits.

    Instruction layers are intentionally not writable through generic
    ``set_data`` patch operations.  This endpoint supports a separate review
    flow by returning a diff only; applying the diff remains an explicit
    operator-controlled source change.
    """
    body = request.get_json(force=True, silent=True) or {}
    source_file = str(body.get("source_file") or "").strip()
    proposed_content = body.get("content")
    if not source_file or not isinstance(proposed_content, str):
        return jsonify({"error": "source_file and content are required"}), 400
    path = (_get_repo_root() / source_file).resolve()
    try:
        path.relative_to(_get_repo_root())
    except ValueError:
        return jsonify({"error": "source_file must stay inside repo root"}), 400
    if path.name != "AGENTS.md":
        return jsonify({"error": "Only AGENTS.md instruction layers are supported"}), 400
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    diff_text = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        proposed_content.splitlines(keepends=True),
        fromfile=f"a/{source_file}",
        tofile=f"b/{source_file}",
    ))
    return jsonify({
        "valid": True,
        "risk_tier": "critical",
        "requires_approval": True,
        "source_file": source_file,
        "diff": diff_text,
        "apply_supported": False,
        "message": "Review-only diff generated; apply through explicit source review.",
    })


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

    cfg = _read_user_json_config()
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

    cfg = _read_user_json_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()

    patch_svc = ConfigGraphPatchService()
    val = patch_svc.validate(graph, ops)

    if not val.valid:
        return jsonify({"error": "Patch validation failed", "details": val.to_dict()}), 422

    if val.requires_approval:
        approval = ConfigGraphApprovalService(
            secret=str(cfg.get("vacge_approval_secret") or "")
        ).validate(
            ops=[op.to_dict() for op in ops],
            risk_tier=val.risk_tier,
            approval_token=approval_token,
        )
        if not approval.approved:
            return jsonify({
                "error": "Patch approval failed",
                "details": {
                    "approval": {
                        "approved": approval.approved,
                        "reason_code": approval.reason_code,
                        "details": approval.details,
                    },
                    "validation": val.to_dict(),
                },
            }), 403

    persistence = ConfigGraphPersistenceService(repo_root=_get_repo_root()).persist(graph, ops)
    if not persistence.success:
        return jsonify({"error": "Patch persistence failed", "details": persistence.to_dict()}), 422

    result = patch_svc.apply(graph, ops, skip_validation=True)
    result.source_diffs = [item.to_dict() for item in persistence.source_diffs]
    result.rollback_artifact = persistence.rollback_artifact

    if not result.success:
        return jsonify({"error": "Patch apply failed", "details": result.to_dict()}), 422

    log_audit("config_graph_patch_applied", {
        "risk_tier": val.risk_tier,
        "op_count": len(ops),
        "source_files": [item.source_file for item in persistence.source_diffs],
        "rollback_sources": len(persistence.rollback_artifact.get("sources") or []),
    })

    refreshed_cfg = _read_user_json_config()
    refreshed_graph = get_config_graph_builder_service(user_config=refreshed_cfg).build()

    return jsonify({
        "result": result.to_dict(),
        "graph": refreshed_graph.to_dict(),
    })


@config_graph_bp.post("/rollback")
def rollback_patch():
    """Apply a rollback artifact previously returned by apply-patch."""
    body = request.get_json(force=True, silent=True) or {}
    artifact = body.get("rollback_artifact")
    if not isinstance(artifact, dict):
        return jsonify({"error": "rollback_artifact is required"}), 400

    result = ConfigGraphPersistenceService(repo_root=_get_repo_root()).rollback(artifact)
    if not result.success:
        return jsonify({"error": "Rollback failed", "details": result.to_dict()}), 422

    log_audit("config_graph_patch_rolled_back", {
        "source_files": [item.source_file for item in result.source_diffs],
        "source_count": len(result.source_diffs),
    })
    cfg = _read_user_json_config()
    graph = get_config_graph_builder_service(user_config=cfg).build()
    return jsonify({"result": result.to_dict(), "graph": graph.to_dict()})


@config_graph_bp.post("/hub-worker/config")
def update_hub_worker_config():
    """Persist a writable Hub/Worker read-model node via VACGE persistence."""
    body = request.get_json(force=True, silent=True) or {}
    node_id = str(body.get("node_id") or "").strip()
    data = body.get("data")
    if not node_id or not isinstance(data, dict):
        return jsonify({"error": "node_id and data object are required"}), 400

    key = _hub_worker_config_key(node_id)
    if key is None:
        return jsonify({"error": f"node is readonly or unknown: {node_id}"}), 422

    result = ConfigGraphPersistenceService(
        repo_root=_get_repo_root()
    ).persist_user_config_block(key=key, data=data)
    if not result.success:
        return jsonify({"error": "Patch persistence failed", "details": result.to_dict()}), 422

    log_audit("hub_worker_config_updated", {
        "node_id": node_id,
        "config_key": key,
        "source_files": [item.source_file for item in result.source_diffs],
    })
    cfg = _read_user_json_config()
    graph = HubWorkerGraphService().build(
        user_config=cfg,
        path=str(body.get("path") or "").strip() or None,
    )
    return jsonify({"result": result.to_dict(), "graph": graph})


@config_graph_bp.post("/create-config-entry")
def create_config_entry():
    """Create a new configuration entry (path_rule or agent_profile).

    Body
    ----
    { entry_type: "path_rule" | "agent_profile" | "restricted_inference_model" | "restricted_inference_task", data: { ... } }

    Returns the refreshed config graph on success.
    """
    body = request.get_json(force=True, silent=True) or {}
    entry_type = str(body.get("entry_type") or "")
    data = dict(body.get("data") or {})

    if entry_type not in ("path_rule", "agent_profile", "restricted_inference_model", "restricted_inference_task"):
        return jsonify({"error": f"Unknown entry_type: {entry_type!r}"}), 400

    node_id = str(data.get("profile_id") or data.get("path_glob") or data.get("id") or entry_type)
    op = PatchOp(
        op="add_node",
        target=f"{entry_type}::{node_id}",
        data={
            "id": f"{entry_type}::{node_id}",
            "node_type": entry_type,
            "label": node_id,
            "data": data,
        },
    )

    cfg = _read_user_json_config()
    graph = get_config_graph_builder_service(user_config=cfg).build()
    patch_svc = ConfigGraphPatchService()
    val = patch_svc.validate(graph, [op])
    if not val.valid:
        return jsonify({"error": "Patch validation failed", "details": val.to_dict()}), 422
    persistence = ConfigGraphPersistenceService(repo_root=_get_repo_root()).persist(graph, [op])
    if not persistence.success:
        return jsonify({"error": "Patch persistence failed", "details": persistence.to_dict()}), 422

    log_audit("config_graph_entry_created", {
        "entry_type": entry_type,
        "risk_tier": val.risk_tier,
        "source_files": [item.source_file for item in persistence.source_diffs],
    })

    cfg = _read_user_json_config()
    builder = get_config_graph_builder_service(user_config=cfg)
    graph = builder.build()
    return jsonify(graph.to_dict())


def _hub_worker_config_key(node_id: str) -> str | None:
    mapping = {
        "hub::ananta": "hub_worker_routing",
        "worker_instance::ananta-worker": "worker_runtime",
        "worker_instance::opencode": "opencode_runtime",
        "worker_instance::hermes": "hermes_worker_adapter",
    }
    return mapping.get(node_id)


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
