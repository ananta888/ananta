"""Read/planning API for incremental CodeCompass layers."""

from __future__ import annotations

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, api_response
from agent.routes.source_control_access import authorize_route_request
from agent.services.source_control_access_policy import SourceControlAction

codecompass_layers_bp = Blueprint("codecompass_layers", __name__)


@codecompass_layers_bp.before_request
@check_auth
def _authorize():
    return authorize_route_request(action=SourceControlAction.query, resource_kind="task_context", collection=True)


@codecompass_layers_bp.route("/api/codecompass/layers/profiles", methods=["GET"])
def list_layer_profiles():
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    return api_response({"profiles": get_codecompass_layer_service().list_profiles()})


@codecompass_layers_bp.route("/api/codecompass/layers/heads/<profile_id>", methods=["GET"])
def show_layer_head(profile_id: str):
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    return api_response({"head": get_codecompass_layer_service().show_head(profile_id)})


@codecompass_layers_bp.route("/api/codecompass/layers/diff", methods=["POST"])
def diff_layer_manifests():
    body = request.get_json(silent=True) or {}
    if "old_manifest" not in body or "new_manifest" not in body:
        raise BadRequestError("manifests_required")
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    return api_response(get_codecompass_layer_service().diff(body["old_manifest"], body["new_manifest"]))


@codecompass_layers_bp.route("/api/codecompass/layers/update", methods=["POST"])
def plan_or_apply_update():
    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", True))
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    service = get_codecompass_layer_service()
    plan = service.plan_update(
        old_manifest=body.get("old_manifest") or {},
        new_manifest=body.get("new_manifest") or {},
        profile=body.get("profile") or {},
        previous_profile=body.get("previous_profile"),
        profile_id=str(body.get("profile_id") or "default"),
        workspace_id=body.get("workspace_id"),
        repository_id=body.get("repository_id"),
    )
    if dry_run:
        return api_response({"plan": plan, "dry_run": True})
    applied = service.apply_update(plan, body.get("profile") or {}, str(body.get("profile_id") or "default"))
    return api_response({"plan": plan, "result": applied, "dry_run": False})


@codecompass_layers_bp.route("/api/codecompass/layers/compact", methods=["POST"])
def compact_layers():
    body = request.get_json(silent=True) or {}
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    return api_response(
        get_codecompass_layer_service().compact(str(body.get("profile_id") or "default"), dry_run=bool(body.get("dry_run", True)))
    )
