from __future__ import annotations

import re
import time
import uuid
from typing import Any

from flask import Blueprint, g, request
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.db_models import (
    RoleDB,
    TeamDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
    TemplateDB,
)
from agent.models import (
    BlueprintBundleDefinition,
    BlueprintBundleMemberAssignment,
    TeamCreateRequest,
    TeamSetupScrumRequest,
    TeamTypeCreateRequest,
    TeamTypeRoleLinkCreateRequest,
    TeamTypeRoleLinkPatchRequest,
    TeamUpdateRequest,
    ImportPlan, # <-- NEW: for _bundle_plan_error_response
)
from agent.services.blueprint_bundle_service import (
    BUNDLE_SCHEMA_VERSION,
    build_bundle_import_plan,
    export_blueprint_bundle,
    normalize_bundle_mode,
    normalize_bundle_parts,
    validate_bundle_mode_and_parts,
)
from agent.services.blueprint_serializer import _serialize_blueprint, _serialize_blueprint_workflow
from agent.services.blueprint_seed_service import initialize_scrum_artifacts, ensure_default_templates # <-- NEW
from agent.services.team_utils import normalize_team_type_name # <-- NEW
from agent.services.repository_registry import get_repository_registry
from agent.services.seed_blueprint_catalog import get_seed_blueprint_catalog
from agent.services.seed_template_catalog import get_seed_template_catalog
from agent.services.system_prompt_catalog import get_system_prompt_catalog
from agent.services.team_blueprint_instantiation_service import (
    instantiate_blueprint as instantiate_blueprint_service,
)
from agent.services.team_blueprint_persistence_service import (
    persist_blueprint_children as persist_blueprint_children_service,
)
from agent.services.team_blueprint_persistence_service import (
    save_blueprint as save_blueprint_service,
)
from agent.services.team_blueprint_reconciliation_service import (
    reconcile_seed_blueprints as reconcile_seed_blueprints_service,
    reconcile_seed_templates as reconcile_seed_templates_service,
)
from agent.services.team_definition_version_service import (
    build_team_blueprint_diff, # Not used in teams.py anymore
    enrich_blueprint_payload, # Not used in teams.py anymore
    team_definition_metadata, # Not used in teams.py anymore
)
from agent.services.team_system_prompt_reconciliation_service import (
    reconcile_system_prompts as reconcile_system_prompts_service,
)
# The following moved to blueprint_seed_service.py or team_utils.py or route_utils.py:
# from agent.services.team_template_bootstrap_service import (
#     RoleLinkSpec, TemplateBootstrapSpec, ensure_default_templates as ensure_default_templates_service,
# )
from agent.utils import validate_request

from agent.routes.route_utils import _repos, _team_error, _parse_bool_query, _parse_parts_query # <-- NEW
from agent.services.team_blueprint_validation_service import _validate_blueprint_roles, _validate_blueprint_artifacts # <-- NEW

teams_bp = Blueprint("teams", __name__)


# --- Helper functions (extracted to new service modules) ---
# Most blueprint-related helper functions and routes are moved to agent/routes/blueprint_routes.py
# and agent/services/* modules. Only team-specific logic remains here.


# --- _apply_bundle_team_members, _activate_only_team, _bundle_plan_error_response, _apply_team_blueprint_bundle_import
# These helper functions are related to bundle import and application.
# `_bundle_plan_error_response` handles generating an error response for bundle import plans.
# `_resolve_bundle_template_by_name` and `_resolve_bundle_blueprint_role_id` are specific to bundle processing.
# Moving these to a bundle-specific service or to blueprint_routes.py if they are only used there.

# Moving _resolve_bundle_template_by_name and _resolve_bundle_blueprint_role_id to a new helper service.
# For now, inline or keep here. Let's keep them here as they are helpers to _apply_bundle_team_members.

def _resolve_bundle_template_by_name(imported_templates: dict[str, TemplateDB], template_name: str | None) -> TemplateDB | None:
    if not template_name:
        return None
    normalized_name = template_name.strip()
    if not normalized_name:
        return None
    return imported_templates.get(normalized_name) or _repos().template_repo.get_by_name(normalized_name)

def _resolve_bundle_blueprint_role_id(blueprint_roles: list[BlueprintRoleDB], blueprint_role_name: str | None) -> str | None:
    if not blueprint_role_name:
        return None
    normalized_name = blueprint_role_name.strip().lower()
    for blueprint_role in blueprint_roles:
        if blueprint_role.name.strip().lower() == normalized_name:
            return blueprint_role.id
    return None

def _apply_bundle_team_members(team_id: str, team_type_id: str | None, blueprint_roles: list[BlueprintRoleDB], members: list[BlueprintBundleMemberAssignment], imported_templates: dict[str, TemplateDB]) -> tuple[bool, tuple | None]:
    allowed_role_ids = _repos().team_type_role_link_repo.get_allowed_role_ids(team_type_id) if team_type_id else []
    _repos().team_member_repo.delete_by_team(team_id)
    for member in members:
        role_name = (member.role_name or "").strip()
        role = _repos().role_repo.get_by_name(role_name) if role_name else None
        if role is None:
            return False, _team_error("role_not_found", 404, role_name=role_name)
        if allowed_role_ids and role.id not in allowed_role_ids:
            return False, _team_error("invalid_role_for_team_type", 400, role_id=role.id)
        template = _resolve_bundle_template_by_name(imported_templates, member.custom_template_name)
        if member.custom_template_name and template is None:
            return False, _team_error("template_not_found", 404, template_name=member.custom_template_name)
        blueprint_role_id = _resolve_bundle_blueprint_role_id(blueprint_roles, member.blueprint_role_name)
        if member.blueprint_role_name and blueprint_role_id is None:
            return False, _team_error("blueprint_role_not_found", 404, blueprint_role_name=member.blueprint_role_name)
        _repos().team_member_repo.save(
            TeamMemberDB(
                team_id=team_id,
                agent_url=member.agent_url,
                role_id=role.id,
                blueprint_role_id=blueprint_role_id,
                custom_template_id=template.id if template else None,
            )
        )
    return True, None

def _activate_only_team(team_id: str) -> None:
    with Session(engine) as session:
        for other in session.exec(select(TeamDB)).all():
            other.is_active = other.id == team_id
            session.add(other)
        session.commit()

def _bundle_plan_error_response(plan: ImportPlan) -> tuple: # <-- TYPE HINT ADDED
    has_conflict = any(error.get("type") == "conflict" for error in plan.errors)
    return _team_error(
        "bundle_import_conflict" if has_conflict else "bundle_import_invalid",
        409 if has_conflict else 400,
        errors=plan.errors,
        diff=plan.diff,
        summary=plan.summary,
        parts=plan.parts,
        mode=plan.mode,
        schema_version=plan.schema_version,
    )

def _apply_team_blueprint_bundle_import(plan: ImportPlan, bundle: BlueprintBundleDefinition) -> tuple | dict: # <-- TYPE HINT ADDED
    import time

    imported_templates: dict[str, TemplateDB] = {}
    imported_blueprint = None
    imported_roles: list[BlueprintRoleDB] = []
    imported_artifacts: list[BlueprintArtifactDB] = []
    imported_team = None

    for spec in plan.template_specs:
        action = spec["action"]
        template = spec["existing"]
        bundle_template = spec["bundle"]
        if action == "create":
            template = _repos().template_repo.save(
                TemplateDB(
                    name=bundle_template.name.strip(),
                    description=bundle_template.description,
                    prompt_template=bundle_template.prompt_template,
                )
            )
        elif action == "update":
            template.description = bundle_template.description
            template.prompt_template = bundle_template.prompt_template
            template = _repos().template_repo.save(template)
        imported_templates[spec["name"]] = template

    if plan.blueprint_spec and plan.blueprint_spec["action"] in {"create", "update", "unchanged"}:
        blueprint_bundle: BlueprintBundleDefinition = plan.blueprint_spec["bundle"]
        role_definitions = []
        for role in blueprint_bundle.roles:
            template = _resolve_bundle_template_by_name(imported_templates, role.template_name)
            role_definitions.append(
                BlueprintRoleDefinition(
                    name=role.name,
                    description=role.description,
                    template_id=template.id if template else None,
                    sort_order=role.sort_order,
                    is_required=role.is_required,
                    config=role.config or {},
                )
            )
        artifact_definitions = [
            # Ensure execution_artifacts is a list, otherwise skip (removed from models)
            BlueprintArtifactDefinition(
                kind=artifact.kind,
                title=artifact.title,
                description=artifact.description,
                sort_order=artifact.sort_order,
                payload=artifact.payload or {},
            )
            for artifact in blueprint_bundle.artifacts
        ]
        valid, error = _validate_blueprint_roles(role_definitions)
        if not valid:
            return _team_error(error[0], error[1], **error[2])
        valid, error = _validate_blueprint_artifacts(artifact_definitions)
        if not valid:
            return _team_error(error[0], error[1], **error[2])

        imported_blueprint = plan.blueprint_spec["existing"]
        if imported_blueprint is None:
            imported_blueprint = _repos().team_blueprint_repo.save(
                TeamBlueprintDB(
                    name=blueprint_bundle.name.strip(),
                    description=blueprint_bundle.description,
                    base_team_type_name=normalize_team_type_name(blueprint_bundle.base_team_type_name or "") or None,
                    is_seed=False,
                )
            )
        elif plan.blueprint_spec["action"] == "update":
            imported_blueprint.name = blueprint_bundle.name.strip()
            imported_blueprint.description = blueprint_bundle.description
            imported_blueprint.base_team_type_name = normalize_team_type_name(blueprint_bundle.base_team_type_name or "") or None
            imported_blueprint.updated_at = time.time()
            imported_blueprint = _repos().team_blueprint_repo.save(imported_blueprint)
        imported_roles, imported_artifacts = persist_blueprint_children_service(imported_blueprint.id, role_definitions, artifact_definitions)

    if plan.team_spec and plan.team_spec["action"] in {"create", "update", "unchanged"}:
        team_bundle: BlueprintBundleTeamDefinition = plan.team_spec["bundle"]
        target_blueprint = imported_blueprint
        if target_blueprint is None and plan.team_spec.get("blueprint_name"):
            target_blueprint = _repos().team_blueprint_repo.get_by_name(plan.team_spec["blueprint_name"])
        if target_blueprint is None:
            return _team_error("blueprint_not_found", 404, blueprint_name=plan.team_spec.get("blueprint_name"))

        if not imported_roles:
            imported_roles = _repos().blueprint_role_repo.get_by_blueprint(target_blueprint.id)
            imported_artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(target_blueprint.id)

        normalized_type_name = normalize_team_type_name(team_bundle.team_type_name or target_blueprint.base_team_type_name or "")
        team_type = _repos().team_type_repo.get_by_name(normalized_type_name) if normalized_type_name else None
        if normalized_type_name and team_type is None:
            return _team_error("team_type_not_found", 404, team_type_name=normalized_type_name)

        role_templates: dict[str, str] = {}
        for role_name, template_name in (team_bundle.role_templates or {}).items():
            role = _repos().role_repo.get_by_name((role_name or "").strip())
            if role is None:
                return _team_error("role_not_found", 404, role_name=role_name)
            template = _resolve_bundle_template_by_name(imported_templates, template_name)
            if template is None:
                return _team_error("template_not_found", 404, template_name=template_name, role_name=role_name)
            role_templates[role.id] = template.id

        snapshot = _serialize_blueprint(target_blueprint, roles=imported_roles, artifacts=imported_artifacts)
        imported_team = plan.team_spec["existing"]
        if imported_team is None:
            imported_team = TeamDB(
                name=team_bundle.name.strip(),
                description=team_bundle.description,
                team_type_id=team_type.id if team_type else None,
                blueprint_id=target_blueprint.id,
                is_active=team_bundle.is_active,
                role_templates=role_templates,
                blueprint_snapshot=snapshot,
            )
        else:
            imported_team.name = team_bundle.name.strip()
            imported_team.description = team_bundle.description
            imported_team.team_type_id = team_type.id if team_type else None # Type issue fix
            imported_team.blueprint_id = target_blueprint.id
            imported_team.is_active = team_bundle.is_active
            imported_team.role_templates = role_templates
            imported_team.blueprint_snapshot = snapshot
        imported_team = _repos().team_repo.save(imported_team)
        if plan.team_spec.get("include_members"):
            valid_members, error_response = _apply_bundle_team_members(
                imported_team.id,
                team_type.id if team_type else None,
                imported_roles,
                team_bundle.members,
                imported_templates,
            )
            if not valid_members: # noqa: E712
                return error_response
        if imported_team.is_active:
            _activate_only_team(imported_team.id)

    result = {
        "schema_version": plan.schema_version,
        "mode": plan.mode,
        "parts": plan.parts,
        "dry_run": False,
        "diff": plan.diff,
        "summary": plan.summary,
        "templates": [template.model_dump() for template in imported_templates.values()],
    }
    if imported_blueprint is not None:
        result["blueprint"] = _serialize_blueprint(imported_blueprint, roles=imported_roles, artifacts=imported_artifacts)
    if imported_team is not None:
        team_payload = imported_team.model_dump()
        team_payload["members"] = [member.model_dump() for member in _repos().team_member_repo.get_by_team(imported_team.id)]
        result["team"] = team_payload
    return result


@teams_bp.route("/teams", methods=["GET"])
@check_auth
def list_teams():
    repos = _repos()
    user = g.user
    only_active = _parse_bool_query(request.args.get("active"))
    only_user_teams = _parse_bool_query(request.args.get("mine"))
    team_type_name = request.args.get("team_type")

    teams = repos.team_repo.get_all()
    filtered = []
    for team in teams:
        if only_active and not team.is_active:
            continue
        if only_user_teams and not repos.team_member_repo.is_member(team.id, user.id):
            continue
        if team_type_name and team.team_type_name != team_type_name:
            continue
        filtered.append(team)
    return api_response(data=[team.model_dump() for team in filtered])


@teams_bp.route("/teams/<tid>", methods=["GET"])
@check_auth
def get_team(tid: str):
    team_id = str(tid or "").strip()
    if not team_id:
        return _team_error("team_id_required", 400)
    team = _repos().team_repo.get_by_id(team_id)
    if team is None:
        return _team_error("team_not_found", 404, team_id=team_id)
    payload = team.model_dump()
    payload["type"] = TeamTypeDB(name=team.team_type_name or "Unknown")
    payload["members"] = [member.model_dump() for member in _repos().team_member_repo.get_by_team(team_id)]
    return api_response(data=payload)


@teams_bp.route("/teams", methods=["POST"])
@admin_required
def create_team():
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    name = str(payload.name or "").strip()
    if not name:
        return _team_error("name_required", 400)
    if _repos().team_repo.get_by_name(name) is not None:
        return _team_error("name_already_taken", 409, name=name)

    team_type_name = str(payload.team_type_name or "").strip()
    team_type = _repos().team_type_repo.get_by_name(team_type_name) if team_type_name else None
    if team_type_name and team_type is None:
        return _team_error("team_type_not_found", 404, team_type_name=team_type_name)

    blueprint_id = str(payload.blueprint_id or "").strip()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id) if blueprint_id else None
    if blueprint_id and blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)

    team = TeamDB(
        name=name,
        description=str(payload.description or "").strip(),
        team_type_id=team_type.id if team_type else None,
        blueprint_id=blueprint.id if blueprint else None,
        is_active=payload.is_active,
        role_templates=payload.role_templates or {},
        blueprint_snapshot=payload.blueprint_snapshot or {},
    )
    team = _repos().team_repo.save(team)
    log_audit("team_created", team_id=team.id, name=team.name)
    if team.is_active:
        _activate_only_team(team.id)

    return api_response(data=team.model_dump(), code=201)


@teams_bp.route("/teams/<tid>", methods=["PATCH"])
@admin_required
def update_team(tid: str):
    team_id = str(tid or "").strip()
    if not team_id:
        return _team_error("team_id_required", 400)
    team = _repos().team_repo.get_by_id(team_id)
    if team is None:
        return _team_error("team_not_found", 404, team_id=team_id)

    data = request.get_json(silent=True) or {}
    try:
        payload = TeamUpdateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    name = str(payload.name or "").strip()
    if name and _repos().team_repo.get_by_name(name) is not None and name != team.name:
        return _team_error("name_already_taken", 409, name=name)

    team.name = name or team.name
    team.description = payload.description or team.description

    if payload.team_type_name is not None:
        new_type_name = str(payload.team_type_name or "").strip()
        if new_type_name:
            team_type = _repos().team_type_repo.get_by_name(new_type_name)
            if team_type is None:
                return _team_error("team_type_not_found", 404, team_type_name=new_type_name)
            team.team_type_id = team_type.id
        else:
            team.team_type_id = None

    if payload.blueprint_id is not None:
        new_blueprint_id = str(payload.blueprint_id or "").strip()
        if new_blueprint_id:
            blueprint = _repos().team_blueprint_repo.get_by_id(new_blueprint_id)
            if blueprint is None:
                return _team_error("blueprint_not_found", 404, blueprint_id=new_blueprint_id)
            team.blueprint_id = blueprint.id
            team.blueprint_snapshot = (blueprint.model_dump() or {})  # Clear it and reset
        else:
            team.blueprint_id = None
            team.blueprint_snapshot = {}

    if payload.is_active is not None:
        team.is_active = payload.is_active
    if payload.role_templates is not None:
        team.role_templates = payload.role_templates
    if payload.blueprint_snapshot is not None:
        team.blueprint_snapshot = payload.blueprint_snapshot
    team.updated_at = time.time()
    team = _repos().team_repo.save(team)
    if team.is_active:
        _activate_only_team(team.id)
    log_audit("team_updated", team_id=team.id, name=team.name)
    return api_response(data=team.model_dump())


@teams_bp.route("/teams/<tid>", methods=["DELETE"])
@admin_required
def delete_team(tid: str):
    team_id = str(tid or "").strip()
    if not team_id:
        return _team_error("team_id_required", 400)
    team = _repos().team_repo.get_by_id(team_id)
    if team is None:
        return _team_error("team_not_found", 404, team_id=team_id)
    _repos().team_repo.delete(team_id)
    log_audit("team_deleted", team_id=team.id, name=team.name)
    return api_response(message="ok")


@teams_bp.route("/team-types", methods=["GET"])
@check_auth
def list_team_types():
    team_types = _repos().team_type_repo.get_all()
    return api_response(data=[t.model_dump() for t in team_types])


@teams_bp.route("/team-types", methods=["POST"])
@admin_required
def create_team_type():
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamTypeCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)
    name = str(payload.name or "").strip()
    if not name:
        return _team_error("name_required", 400)
    if _repos().team_type_repo.get_by_name(name) is not None:
        return _team_error("name_already_taken", 409, name=name)
    team_type = TeamTypeDB(name=name, description=str(payload.description or "").strip())
    team_type = _repos().team_type_repo.save(team_type)
    return api_response(data=team_type.model_dump(), code=201)


@teams_bp.route("/team-types/<ttid>", methods=["PATCH"])
@admin_required
def update_team_type(ttid: str):
    team_type_id = str(ttid or "").strip()
    if not team_type_id:
        return _team_error("team_type_id_required", 400)
    team_type = _repos().team_type_repo.get_by_id(team_type_id)
    if team_type is None:
        return _team_error("team_type_not_found", 404, team_type_id=team_type_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamTypeCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)
    name = str(payload.name or "").strip()
    if name and _repos().team_type_repo.get_by_name(name) is not None and name != team_type.name:
        return _team_error("name_already_taken", 409, name=name)
    team_type.name = name or team_type.name
    team_type.description = payload.description or team_type.description
    team_type = _repos().team_type_repo.save(team_type)
    return api_response(data=team_type.model_dump())


@teams_bp.route("/team-types/<ttid>", methods=["DELETE"])
@admin_required
def delete_team_type(ttid: str):
    team_type_id = str(ttid or "").strip()
    if not team_type_id:
        return _team_error("team_type_id_required", 400)
    team_type = _repos().team_type_repo.get_by_id(team_type_id)
    if team_type is None:
        return _team_error("team_type_not_found", 404, team_type_id=team_type_id)
    _repos().team_type_repo.delete(team_type_id)
    return api_response(message="ok")


@teams_bp.route("/team-types/<ttid>/roles", methods=["GET"])
@check_auth
def list_team_type_roles(ttid: str):
    team_type_id = str(ttid or "").strip()
    if not team_type_id:
        return _team_error("team_type_id_required", 400)
    team_type = _repos().team_type_repo.get_by_id(team_type_id)
    if team_type is None:
        return _team_error("team_type_not_found", 404, team_type_id=team_type_id)
    roles = _repos().team_type_role_link_repo.get_by_team_type(team_type_id)
    return api_response(data=[link.model_dump() for link in roles])


@teams_bp.route("/team-types/<ttid>/roles", methods=["POST"])
@admin_required
def create_team_type_role(ttid: str):
    team_type_id = str(ttid or "").strip()
    if not team_type_id:
        return _team_error("team_type_id_required", 400)
    team_type = _repos().team_type_repo.get_by_id(team_type_id)
    if team_type is None:
        return _team_error("team_type_not_found", 404, team_type_id=team_type_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamTypeRoleLinkCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)
    role_id = str(payload.role_id or "").strip()
    if not role_id:
        return _team_error("role_id_required", 400)
    role = _repos().role_repo.get_by_id(role_id)
    if role is None:
        return _team_error("role_not_found", 404, role_id=role_id)
    if _repos().team_type_role_link_repo.get_by_team_type_and_role(team_type_id, role_id) is not None:
        return _team_error("role_already_linked", 409, role_id=role_id, team_type_id=team_type_id)
    link = TeamTypeRoleLink(team_type_id=team_type_id, role_id=role_id)
    link = _repos().team_type_role_link_repo.save(link)
    return api_response(data=link.model_dump(), code=201)


@teams_bp.route("/team-types/<ttid>/roles/<rid>", methods=["PATCH"])
@admin_required
def update_team_type_role(ttid: str, rid: str):
    team_type_id = str(ttid or "").strip()
    role_id = str(rid or "").strip()
    if not team_type_id:
        return _team_error("team_type_id_required", 400)
    if not role_id:
        return _team_error("role_id_required", 400)
    team_type = _repos().team_type_repo.get_by_id(team_type_id)
    if team_type is None:
        return _team_error("team_type_not_found", 404, team_type_id=team_type_id)
    role = _repos().role_repo.get_by_id(role_id)
    if role is None:
        return _team_error("role_not_found", 404, role_id=role_id)
    link = _repos().team_type_role_link_repo.get_by_team_type_and_role(team_type_id, role_id)
    if link is None:
        return _team_error("link_not_found", 404, role_id=role_id, team_type_id=team_type_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamTypeRoleLinkPatchRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    # Currently no updatable fields on TeamTypeRoleLink, but structure for future.

    link = _repos().team_type_role_link_repo.save(link)
    return api_response(data=link.model_dump())


@teams_bp.route("/team-types/<ttid>/roles/<rid>", methods=["DELETE"])
@admin_required
def delete_team_type_role(ttid: str, rid: str):
    team_type_id = str(ttid or "").strip()
    role_id = str(rid or "").strip()
    if not team_type_id:
        return _team_error("team_type_id_required", 400)
    if not role_id:
        return _team_error("role_id_required", 400)
    team_type = _repos().team_type_repo.get_by_id(team_type_id)
    if team_type is None:
        return _team_error("team_type_not_found", 404, team_type_id=team_type_id)
    role = _repos().role_repo.get_by_id(role_id)
    if role is None:
        return _team_error("role_not_found", 404, role_id=role_id)
    link = _repos().team_type_role_link_repo.get_by_team_type_and_role(team_type_id, role_id)
    if link is None:
        return _team_error("link_not_found", 404, role_id=role_id, team_type_id=team_type_id)
    _repos().team_type_role_link_repo.delete(link.id)
    return api_response(message="ok")


@teams_bp.route("/roles", methods=["GET"])
@check_auth
def list_roles():
    roles = _repos().role_repo.get_all()
    return api_response(data=[r.model_dump() for r in roles])


@teams_bp.route("/roles", methods=["POST"])
@admin_required
def create_role():
    data = request.get_json(silent=True) or {}
    try:
        payload = RoleCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)
    name = str(payload.name or "").strip()
    if not name:
        return _team_error("name_required", 400)
    if _repos().role_repo.get_by_name(name) is not None:
        return _team_error("name_already_taken", 409, name=name)
    role = RoleDB(name=name, description=str(payload.description or "").strip(), default_template_id=payload.default_template_id)
    role = _repos().role_repo.save(role)
    return api_response(data=role.model_dump(), code=201)


@teams_bp.route("/roles/<rid>", methods=["PATCH"])
@admin_required
def update_role(rid: str):
    role_id = str(rid or "").strip()
    if not role_id:
        return _team_error("role_id_required", 400)
    role = _repos().role_repo.get_by_id(role_id)
    if role is None:
        return _team_error("role_not_found", 404, role_id=role_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = RoleCreateRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)
    name = str(payload.name or "").strip()
    if name and _repos().role_repo.get_by_name(name) is not None and name != role.name:
        return _team_error("name_already_taken", 409, name=name)
    role.name = name or role.name
    role.description = payload.description or role.description
    role.default_template_id = payload.default_template_id or role.default_template_id
    role = _repos().role_repo.save(role)
    return api_response(data=role.model_dump())


@teams_bp.route("/roles/<rid>", methods=["DELETE"])
@admin_required
def delete_role(rid: str):
    role_id = str(rid or "").strip()
    if not role_id:
        return _team_error("role_id_required", 400)
    role = _repos().role_repo.get_by_id(role_id)
    if role is None:
        return _team_error("role_not_found", 404, role_id=role_id)
    _repos().role_repo.delete(role_id)
    return api_response(message="ok")


@teams_bp.route("/bundle/import", methods=["POST"])
@admin_required
def import_blueprint_bundle():
    data = request.get_json(silent=True) or {}
    try:
        bundle = BlueprintBundleDefinition.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    mode = normalize_bundle_mode(request.args.get("mode"))
    parts = normalize_bundle_parts(request.args.get("parts"))
    valid, errors = validate_bundle_mode_and_parts(mode, parts)
    if not valid:
        return _team_error("invalid_mode_or_parts", 400, errors=errors)

    plan = build_bundle_import_plan(bundle, mode=mode, parts=parts, repos=_repos())

    if not plan.all_valid:  # noqa: E712
        return _bundle_plan_error_response(plan)

    if plan.dry_run:
        return api_response(data=plan.model_dump())

    response = _apply_team_blueprint_bundle_import(plan, bundle)
    if isinstance(response, tuple):
        return response
    response.setdefault("schema_version", BUNDLE_SCHEMA_VERSION)
    return api_response(data=response, code=201)


@teams_bp.route("/bundle/export/<bpid>", methods=["GET"])
@check_auth
def export_blueprint(bpid: str):
    blueprint_id = str(bpid or "").strip()
    if not blueprint_id:
        return _team_error("blueprint_id_required", 400)
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if blueprint is None:
        return _team_error("blueprint_not_found", 404, blueprint_id=blueprint_id)
    return api_response(data=export_blueprint_bundle(blueprint_id, repos=_repos()))


@teams_bp.route("/setup/scrum", methods=["POST"])
@admin_required
def setup_scrum():
    data = request.get_json(silent=True) or {}
    try:
        payload = TeamSetupScrumRequest.model_validate(data)
    except Exception:
        return _team_error("invalid_payload", 400)

    team_name = str(payload.team_name or "Scrum Team").strip()
    if _repos().team_repo.get_by_name(team_name) is not None:
        return _team_error("team_name_already_taken", 409, team_name=team_name)

    # Ensure team type, roles, templates etc. exist
    ensure_default_templates("Scrum")

    # Create the team
    scrum_team_type = _repos().team_type_repo.get_by_name("Scrum")
    team = TeamDB(
        name=team_name,
        description=str(payload.team_description or "Default Scrum Team").strip(),
        team_type_id=scrum_team_type.id if scrum_team_type else None,
        is_active=True,
    )
    team = _repos().team_repo.save(team)

    # Create members
    roles = _repos().role_repo.get_all()
    for role_payload in [
        {"name": "Product Owner", "agent_url": payload.product_owner_agent_url},
        {"name": "Scrum Master", "agent_url": payload.scrum_master_agent_url},
        {"name": "AI Developer", "agent_url": payload.ai_developer_agent_url},
        {"name": "Observer", "agent_url": ""},
    ]:
        role_name = str(role_payload["name"]).strip()
        agent_url = str(role_payload["agent_url"] or "").strip()
        role = next((r for r in roles if r.name == role_name), None)
        if role is None:
            return _team_error("role_not_found", 404, role_name=role_name)
        _repos().team_member_repo.save(
            TeamMemberDB(
                team_id=team.id,
                role_id=role.id,
                agent_url=agent_url,
            )
        )

    # Initial tasks
    initialize_scrum_artifacts(team_name)

    _activate_only_team(team.id)
    log_audit("scrum_setup_completed", team_id=team.id, team_name=team.name)
    return api_response(data=team.model_dump(), code=201)


@teams_bp.route("/system-prompts", methods=["POST"])
@admin_required
def ensure_system_prompts():
    reconcile_system_prompts_service(force=False)
    return api_response(message="System prompts reconciled.")
