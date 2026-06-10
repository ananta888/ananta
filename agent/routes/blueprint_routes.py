from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, g, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.db_models import BlueprintArtifactDB, BlueprintRoleDB, TeamBlueprintDB, TeamTypeDB, TemplateDB
from agent.models import (
    BlueprintArtifactDefinition,
    BlueprintRoleDefinition,
    TeamBlueprintCreateRequest,
    TeamBlueprintInstantiateRequest,
    TeamBlueprintUpdateRequest,
)
from agent.services.blueprint_serializer import _serialize_blueprint
from agent.services.repository_registry import get_repository_registry as _repos
from agent.services.team_blueprint_instantiation_service import (
    instantiate_blueprint as instantiate_blueprint_service,
)
from agent.services.team_blueprint_persistence_service import (
    save_blueprint as save_blueprint_service,
)
from agent.services.team_blueprint_validation_service import _validate_blueprint_artifacts, _validate_blueprint_roles
from agent.services.team_definition_version_service import enrich_blueprint_payload, team_definition_metadata
from agent.services.team_utils import normalize_team_type_name

blueprint_bp = Blueprint("blueprints", __name__)


def _team_error(message: str, code: int, **extra):
    """Return standardized API response with legacy compatibility."""
    return api_response(status="error", message=message, code=code, data=extra if extra else None)


def _parse_bool_query(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@blueprint_bp.route("/blueprints", methods=["GET"])
@check_auth
def list_team_blueprints():
    only_seed = _parse_bool_query(request.args.get("seed"))
    blueprints = _repos().team_blueprint_repo.get_all()
    filtered = []
    for bp in blueprints:
        if only_seed and not bp.is_seed:
            continue
        filtered.append(bp)
    return api_response(data=[bp.model_dump() for bp in filtered])


@blueprint_bp.route("/blueprints/<bpid>", methods=["GET"])
@check_auth
def get_team_blueprint(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)

    payload = _serialize_blueprint(blueprint)
    return api_response(data=payload)


@blueprint_bp.route("/blueprints/<bpid>/catalog-item", methods=["GET"])
@check_auth
def get_team_blueprint_catalog_item(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)
    payload = team_definition_metadata(blueprint, _repos())
    return api_response(data=payload)


@blueprint_bp.route("/blueprints/<bpid>/work-profile", methods=["GET"])
@check_auth
def get_team_blueprint_work_profile(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)

    roles = _repos().blueprint_role_repo.get_by_blueprint(blueprint_id)
    artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(blueprint_id)
    team_type = _repos().team_type_repo.get_by_id(blueprint.base_team_type_id) if blueprint.base_team_type_id else None

    profile_dict = {
        "blueprint_id": blueprint_id,
        "blueprint_name": blueprint.name,
        "team_type_name": team_type.name if team_type else None,
        "roles": [],
        "artifacts": [],
    }

    for role in roles:
        role_dict = role.model_dump()
        template = _repos().template_repo.get_by_id(role.template_id) if role.template_id else None
        role_dict["template"] = template.model_dump() if template else None
        profile_dict["roles"].append(role_dict)

    for artifact in artifacts:
        profile_dict["artifacts"].append(artifact.model_dump())

    return api_response(data=profile_dict)


@blueprint_bp.route("/blueprints", methods=["POST"])
@admin_required
def create_team_blueprint():
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamBlueprintCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    name = str(payload.name or "").strip()
    if not name:
        return _team_error("name_required", 400)
    if _repos().team_blueprint_repo.get_by_name(name) is not None:
        return _team_error("name_already_taken", 409, name=name)

    base_team_type_name = str(payload.base_team_type_name or "").strip()
    base_team_type = _repos().team_type_repo.get_by_name(base_team_type_name) if base_team_type_name else None
    if base_team_type_name and base_team_type is None:
        return _team_error("base_team_type_not_found", 404, base_team_type_name=base_team_type_name)

    blueprint = TeamBlueprintDB(
        name=name,
        description=str(payload.description or "").strip(),
        base_team_type_id=base_team_type.id if base_team_type else None,
        is_seed=False,
    )
    blueprint = save_blueprint_service(blueprint, payload.roles, payload.artifacts)
    return api_response(data=_serialize_blueprint(blueprint), code=201)


@blueprint_bp.route("/blueprints/<bpid>", methods=["PATCH"])
@admin_required
def update_team_blueprint(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamBlueprintUpdateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    name = str(payload.name or "").strip()
    if name and _repos().team_blueprint_repo.get_by_name(name) is not None and name != blueprint.name:
        return _team_error("name_already_taken", 409, name=name)

    blueprint.name = name or blueprint.name
    blueprint.description = payload.description or blueprint.description

    if payload.base_team_type_name is not None:
        new_type_name = str(payload.base_team_type_name or "").strip()
        if new_type_name:
            base_team_type = _repos().team_type_repo.get_by_name(new_type_name)
            if base_team_type is None:
                return _team_error("base_team_type_not_found", 404, base_team_type_name=new_type_name)
            blueprint.base_team_type_id = base_team_type.id
        else:
            blueprint.base_team_type_id = None

    blueprint.updated_at = time.time()
    blueprint = save_blueprint_service(blueprint, payload.roles, payload.artifacts)
    return api_response(data=_serialize_blueprint(blueprint))


@blueprint_bp.route("/blueprints/<bpid>", methods=["DELETE"])
@admin_required
def delete_team_blueprint(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)
    _repos().team_blueprint_repo.delete(blueprint_id)
    return api_response(message="ok")


@blueprint_bp.route("/blueprints/<bpid>/instantiate", methods=["POST"])
@admin_required
def instantiate_blueprint(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamBlueprintInstantiateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)
    team = instantiate_blueprint_service(
        blueprint_id,
        team_name=payload.team_name,
        team_description=payload.team_description,
        is_active=payload.is_active,
    )
    return api_response(data=team.model_dump(), code=201)


@blueprint_bp.route("/seed-data/blueprints", methods=["POST"])
@admin_required
def ensure_seed_blueprints():
    # Only if empty
    if _repos().team_blueprint_repo.count_all() > 0:
        return api_response(message="blueprints_already_exist", code=200)
    reconcile_seed_blueprints_service()
    reconcile_seed_templates_service()
    reconcile_system_prompts_service()
    return api_response(message="ok", code=201)


@blueprint_bp.route("/bundle/export/<bpid>", methods=["GET"])
@check_auth
def export_blueprint(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)
    return api_response(data=export_blueprint_bundle(blueprint_id, repos=_repos()))