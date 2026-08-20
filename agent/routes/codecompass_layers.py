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
    action = (
        SourceControlAction.index
        if request.endpoint in {"codecompass_layers.plan_or_apply_update", "codecompass_layers.compact_layers"}
        else SourceControlAction.query
    )
    return authorize_route_request(
        action=action,
        resource_kind="task_context",
        collection=True,
        require_project_scope=True,
    )


def _object_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise BadRequestError("invalid_json_object")
    return body


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
    body = _object_body()
    if "from_snapshot_ref" not in body or "to_snapshot_ref" not in body:
        raise BadRequestError("snapshot_refs_required")
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    return api_response(get_codecompass_layer_service().diff(**body))


@codecompass_layers_bp.route("/api/codecompass/layers/update", methods=["POST"])
def plan_or_apply_update():
    body = _object_body()
    if "dry_run" in body and not isinstance(body["dry_run"], bool):
        raise BadRequestError("dry_run_boolean_required")
    dry_run = body.get("dry_run", True)
    required = {"from_snapshot_ref", "to_snapshot_ref", "profile_ref", "profile_id"}
    if not required.issubset(body):
        raise BadRequestError("authoritative_layer_refs_required")
    if not dry_run and not {"expected_generation", "idempotency_key"}.issubset(body):
        raise BadRequestError("generation_and_idempotency_required")
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    service = get_codecompass_layer_service()
    plan = service.plan_update(**{key: value for key, value in body.items() if key != "dry_run"})
    if dry_run:
        return api_response({"plan": plan, "dry_run": True})
    applied = service.apply_update(
        plan=plan,
        profile_ref=body["profile_ref"],
        profile_id=str(body["profile_id"]),
        expected_generation=int(body["expected_generation"]),
        idempotency_key=str(body["idempotency_key"]),
    )
    return api_response({"plan": plan, "result": applied, "dry_run": False})


@codecompass_layers_bp.route("/api/codecompass/layers/compact", methods=["POST"])
def compact_layers():
    body = _object_body()
    if "dry_run" in body and not isinstance(body["dry_run"], bool):
        raise BadRequestError("dry_run_boolean_required")
    if not body.get("profile_id"):
        raise BadRequestError("profile_id_required")
    dry_run = body.get("dry_run", True)
    if not dry_run and not {"expected_generation", "idempotency_key"}.issubset(body):
        raise BadRequestError("generation_and_idempotency_required")
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    return api_response(
        get_codecompass_layer_service().compact(
            profile_id=str(body["profile_id"]),
            expected_generation=body.get("expected_generation"),
            idempotency_key=body.get("idempotency_key"),
            dry_run=dry_run,
        )
    )
