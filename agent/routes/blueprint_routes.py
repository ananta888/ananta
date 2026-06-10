"""Team-blueprint routes (/teams/blueprints/*) and bundle import/export.

Extracted from agent/routes/teams.py (SPLIT-012). Route paths are unchanged.
"""

import re
import time

from flask import Blueprint, g, request
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import (
    BlueprintArtifactDB,
    BlueprintRoleDB,
    TeamBlueprintDB,
    TeamDB,
    TeamMemberDB,
    TemplateDB,
)
from agent.models import (
    BlueprintArtifactDefinition,
    BlueprintBundleDefinition,
    BlueprintBundleMemberAssignment,
    BlueprintBundleTeamDefinition,
    BlueprintRoleDefinition,
    TeamBlueprintBundleImportRequest,
    TeamBlueprintCreateRequest,
    TeamBlueprintInstantiateRequest,
    TeamBlueprintUpdateRequest,
)
from agent.routes.route_utils import parse_bool_query, parse_parts_query, team_error as _team_error
from agent.services.blueprint_bundle_service import (
    BUNDLE_SCHEMA_VERSION,
    build_bundle_import_plan,
    export_blueprint_bundle,
    normalize_bundle_mode,
    normalize_bundle_parts,
    validate_bundle_mode_and_parts,
)
from agent.services.blueprint_seed_service import (
    ensure_default_templates,
    ensure_seed_blueprints,
    normalize_team_type_name,
)
from agent.services.blueprint_serializer import (
    _blueprint_catalog_sort_key,
    _build_blueprint_work_profile,
    _serialize_blueprint,
    _serialize_blueprint_catalog_item,
    _user_lifecycle_state_from_metadata,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.team_blueprint_instantiation_service import (
    instantiate_blueprint as instantiate_blueprint_service,
)
from agent.services.team_blueprint_persistence_service import (
    persist_blueprint_children as persist_blueprint_children_service,
)
from agent.services.team_blueprint_persistence_service import (
    save_blueprint as save_blueprint_service,
)
from agent.services.team_definition_version_service import team_definition_metadata
from agent.utils import validate_request

blueprint_bp = Blueprint("team_blueprints", __name__)


def _repos():
    return get_repository_registry()


def _bundle_plan_error_response(plan) -> tuple:
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


def _apply_team_blueprint_bundle_import(plan, bundle) -> tuple | dict:
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
        imported_roles, imported_artifacts = _persist_blueprint_children(imported_blueprint.id, role_definitions, artifact_definitions)

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
            imported_team.team_type_id = team_type.id if team_type else None
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
            if not valid_members:
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


ALLOWED_BLUEPRINT_ARTIFACT_KINDS = {"task", "policy"}


def _validate_blueprint_roles(roles: list) -> tuple[bool, tuple | None]:
    seen_names: set[str] = set()
    seen_sort_orders: set[int] = set()
    for role in roles:
        normalized_name = role.name.strip()
        if not normalized_name:
            return False, ("blueprint_role_name_required", 400, {})
        if normalized_name.lower() in seen_names:
            return False, ("duplicate_blueprint_role_name", 400, {"role_name": normalized_name})
        seen_names.add(normalized_name.lower())
        if role.sort_order in seen_sort_orders:
            return False, ("duplicate_blueprint_role_sort_order", 400, {"sort_order": role.sort_order})
        seen_sort_orders.add(role.sort_order)
        if role.template_id and not _repos().template_repo.get_by_id(role.template_id):
            return False, ("template_not_found", 404, {"template_id": role.template_id})
        role_config = dict(role.config or {})
        capability_defaults = role_config.get("capability_defaults")
        risk_profile = role_config.get("risk_profile")
        verification_defaults = role_config.get("verification_defaults")
        execution_mode = role_config.get("execution_mode")
        # DRR-T053: Validate worker_selection and runtime_target policy
        worker_selection = role_config.get("worker_selection")
        if worker_selection is not None:
            from agent.services.worker_selection_policy_service import WorkerSelectionPolicyService
            _, err = WorkerSelectionPolicyService().validate_or_error({"worker_selection": worker_selection})
            if err:
                return False, ("blueprint_role_worker_selection_invalid", 400, {"role_name": normalized_name, "detail": err})

        runtime_target = role_config.get("runtime_target")
        if runtime_target is not None:
            if not isinstance(runtime_target, dict):
                 return False, ("blueprint_role_runtime_target_invalid", 400, {"role_name": normalized_name, "detail": "must be a dictionary"})
            from agent.services.worker_runtime_target_service import WorkerRuntimeTargetService
            try:
                WorkerRuntimeTargetService().from_config(runtime_target)
            except Exception as exc:
                return False, ("blueprint_role_runtime_target_invalid", 400, {"role_name": normalized_name, "detail": str(exc)})

        preferred_backend = role_config.get("preferred_backend")
        if capability_defaults is not None and not isinstance(capability_defaults, list):
            return False, ("blueprint_role_capability_defaults_invalid", 400, {"role_name": normalized_name})
        if risk_profile is not None and str(risk_profile).strip().lower() not in {"low", "balanced", "high", "strict"}:
            return False, ("blueprint_role_risk_profile_invalid", 400, {"role_name": normalized_name})
        if verification_defaults is not None and not isinstance(verification_defaults, dict):
            return False, ("blueprint_role_verification_defaults_invalid", 400, {"role_name": normalized_name})
        if execution_mode is not None and not re.fullmatch(r"[a-z][a-z0-9_]*", str(execution_mode or "")):
            return False, ("blueprint_role_execution_mode_invalid", 400, {"role_name": normalized_name})
        if preferred_backend is not None and not re.fullmatch(r"[a-z][a-z0-9_]*", str(preferred_backend or "")):
            return False, ("blueprint_role_preferred_backend_invalid", 400, {"role_name": normalized_name})
    return True, None


def _validate_blueprint_artifacts(artifacts: list) -> tuple[bool, tuple | None]:
    seen_titles: set[str] = set()
    seen_sort_orders: set[int] = set()
    for artifact in artifacts:
        normalized_kind = artifact.kind.strip().lower()
        normalized_title = artifact.title.strip()
        if not normalized_kind:
            return False, ("blueprint_artifact_kind_required", 400, {})
        if normalized_kind not in ALLOWED_BLUEPRINT_ARTIFACT_KINDS:
            return False, (
                "blueprint_artifact_kind_invalid",
                400,
                {"kind": artifact.kind, "allowed_kinds": sorted(ALLOWED_BLUEPRINT_ARTIFACT_KINDS)},
            )
        if not normalized_title:
            return False, ("blueprint_artifact_title_required", 400, {})
        if normalized_title.lower() in seen_titles:
            return False, ("duplicate_blueprint_artifact_title", 400, {"title": normalized_title})
        seen_titles.add(normalized_title.lower())
        if artifact.sort_order in seen_sort_orders:
            return False, ("duplicate_blueprint_artifact_sort_order", 400, {"sort_order": artifact.sort_order})
        seen_sort_orders.add(artifact.sort_order)
    return True, None


def _persist_blueprint_children(
    blueprint_id: str,
    role_definitions: list | None,
    artifact_definitions: list | None,
) -> tuple[list[BlueprintRoleDB], list[BlueprintArtifactDB]]:
    return persist_blueprint_children_service(blueprint_id, role_definitions, artifact_definitions)


def _instantiate_blueprint(blueprint: TeamBlueprintDB, data: TeamBlueprintInstantiateRequest) -> TeamDB | tuple:
    normalized_type_name = normalize_team_type_name(blueprint.base_team_type_name or "")
    if normalized_type_name:
        ensure_default_templates(normalized_type_name)
    return instantiate_blueprint_service(
        blueprint.id,
        data,
        error_factory=_team_error,
        normalize_team_type_name=normalize_team_type_name,
    )


@blueprint_bp.route("/teams/blueprints", methods=["GET"])
@check_auth
def list_team_blueprints():
    ensure_seed_blueprints()
    blueprints = _repos().team_blueprint_repo.get_all()
    return api_response(data=[_serialize_blueprint(blueprint) for blueprint in blueprints])


@blueprint_bp.route("/teams/blueprints/catalog", methods=["GET"])
@check_auth
def list_team_blueprint_catalog():
    ensure_seed_blueprints()
    blueprints = _repos().team_blueprint_repo.get_all()
    items = []
    for blueprint in blueprints:
        roles = _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
        artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
        items.append(_serialize_blueprint_catalog_item(blueprint, roles, artifacts))
    items.sort(key=_blueprint_catalog_sort_key)
    return api_response(
        data={
            "public_model": {
                "template_term": "Role Template",
                "template_api_term": "template",
                "blueprint_term": "Blueprint",
                "team_term": "Team",
                "default_entry_path": "Start with a blueprint, then instantiate a team.",
                "advanced_concepts": ["snapshot", "drift", "reconcile"],
            },
            "items": items,
        }
    )


@blueprint_bp.route("/teams/blueprints/<blueprint_id>", methods=["GET"])
@check_auth
def get_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    return api_response(data=_serialize_blueprint(blueprint))


@blueprint_bp.route("/teams/blueprints/<blueprint_id>/work-profile", methods=["GET"])
@check_auth
def get_team_blueprint_work_profile(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    roles = _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
    artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    return api_response(data=_build_blueprint_work_profile(blueprint, roles, artifacts))


@blueprint_bp.route("/teams/blueprints", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintCreateRequest)
def create_team_blueprint():
    data: TeamBlueprintCreateRequest = g.validated_data
    blueprint_name = data.name.strip()
    if not blueprint_name:
        return _team_error("blueprint_name_required", 400)
    if _repos().team_blueprint_repo.get_by_name(blueprint_name):
        return _team_error("blueprint_name_exists", 409, name=blueprint_name)

    valid, error = _validate_blueprint_roles(data.roles)
    if not valid:
        return _team_error(error[0], error[1], **error[2])
    valid, error = _validate_blueprint_artifacts(data.artifacts)
    if not valid:
        return _team_error(error[0], error[1], **error[2])

    normalized_type_name = normalize_team_type_name(data.base_team_type_name or "")
    if normalized_type_name:
        ensure_default_templates(normalized_type_name)

    result = save_blueprint_service(
        blueprint_id=None,
        name=blueprint_name,
        description=data.description,
        base_team_type_name=normalized_type_name or None,
        roles=data.roles,
        artifacts=data.artifacts,
        is_seed=False,
    )
    log_audit(
        "team_blueprint_created",
        {"blueprint_id": result.blueprint.id, "name": result.blueprint.name, "changes": result.changes},
    )
    return api_response(data=_serialize_blueprint(result.blueprint, roles=result.roles, artifacts=result.artifacts), code=201)


@blueprint_bp.route("/teams/blueprints/<blueprint_id>", methods=["PATCH"])
@check_auth
@admin_required
@validate_request(TeamBlueprintUpdateRequest)
def update_team_blueprint(blueprint_id):
    data: TeamBlueprintUpdateRequest = g.validated_data
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)

    if data.name is not None and data.name.strip() != blueprint.name:
        if not data.name.strip():
            return _team_error("blueprint_name_required", 400)
        existing = _repos().team_blueprint_repo.get_by_name(data.name.strip())
        if existing and existing.id != blueprint_id:
            return _team_error("blueprint_name_exists", 409, name=data.name.strip())
        blueprint.name = data.name.strip()
    if data.description is not None:
        blueprint.description = data.description
    if data.base_team_type_name is not None:
        normalized_type_name = normalize_team_type_name(data.base_team_type_name)
        if normalized_type_name:
            ensure_default_templates(normalized_type_name)
        blueprint.base_team_type_name = normalized_type_name or None

    if data.roles is not None:
        valid, error = _validate_blueprint_roles(data.roles)
        if not valid:
            return _team_error(error[0], error[1], **error[2])
    if data.artifacts is not None:
        valid, error = _validate_blueprint_artifacts(data.artifacts)
        if not valid:
            return _team_error(error[0], error[1], **error[2])

    result = save_blueprint_service(
        blueprint_id=blueprint.id,
        name=blueprint.name,
        description=blueprint.description,
        base_team_type_name=blueprint.base_team_type_name,
        roles=data.roles,
        artifacts=data.artifacts,
        is_seed=blueprint.is_seed,
    )
    log_audit(
        "team_blueprint_updated",
        {"blueprint_id": result.blueprint.id, "name": result.blueprint.name, "changes": result.changes},
    )
    return api_response(data=_serialize_blueprint(result.blueprint, roles=result.roles, artifacts=result.artifacts))


@blueprint_bp.route("/teams/blueprints/<blueprint_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_blueprint(blueprint_id):
    with Session(engine) as session:
        referencing_teams = session.exec(select(TeamDB).where(TeamDB.blueprint_id == blueprint_id)).all()
    if referencing_teams:
        return _team_error(
            "blueprint_in_use",
            409,
            blueprint_id=blueprint_id,
            team_ids=[team.id for team in referencing_teams],
            team_count=len(referencing_teams),
        )
    _repos().blueprint_artifact_repo.delete_by_blueprint(blueprint_id)
    _repos().blueprint_role_repo.delete_by_blueprint(blueprint_id)
    if _repos().team_blueprint_repo.delete(blueprint_id):
        log_audit("team_blueprint_deleted", {"blueprint_id": blueprint_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@blueprint_bp.route("/teams/blueprints/<blueprint_id>/bundle", methods=["GET"])
@check_auth
@admin_required
def export_team_blueprint_bundle(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    mode = normalize_bundle_mode(request.args.get("mode"))
    parts = normalize_bundle_parts(parse_parts_query(request.args.get("parts")), [])
    errors = validate_bundle_mode_and_parts(mode, parts)
    if errors:
        return _team_error("bundle_export_invalid", 400, errors=errors)

    team = None
    team_id = request.args.get("team_id")
    if team_id:
        team = _repos().team_repo.get_by_id(team_id)
        if not team:
            return _team_error("team_not_found", 404, team_id=team_id)
        if team.blueprint_id != blueprint_id:
            return _team_error("team_blueprint_mismatch", 400, team_id=team_id, blueprint_id=blueprint_id)

    payload = export_blueprint_bundle(
        _repos(),
        blueprint,
        team=team,
        include_members=parse_bool_query(request.args.get("include_members")),
        mode=mode,
        parts=parts,
    )
    return api_response(data=payload)


@blueprint_bp.route("/teams/blueprints/import", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintBundleImportRequest)
def import_team_blueprint_bundle():
    data: TeamBlueprintBundleImportRequest = g.validated_data
    plan = build_bundle_import_plan(_repos(), data.bundle, data.conflict_strategy)
    if plan.errors:
        return _bundle_plan_error_response(plan)
    if data.dry_run:
        return api_response(
            data={
                "schema_version": plan.schema_version,
                "mode": plan.mode,
                "parts": plan.parts,
                "dry_run": True,
                "diff": plan.diff,
                "summary": plan.summary,
            }
        )

    result = _apply_team_blueprint_bundle_import(plan, data.bundle)
    if isinstance(result, tuple):
        return result
    log_audit(
        "team_blueprint_bundle_imported",
        {
            "mode": plan.mode,
            "parts": plan.parts,
            "conflict_strategy": plan.conflict_strategy,
            "schema_version": BUNDLE_SCHEMA_VERSION,
        },
    )
    return api_response(data=result)


@blueprint_bp.route("/teams/blueprints/<blueprint_id>/instantiate", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintInstantiateRequest)
def instantiate_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)

    data: TeamBlueprintInstantiateRequest = g.validated_data
    instantiated = _instantiate_blueprint(blueprint, data)
    if isinstance(instantiated, tuple):
        return instantiated

    log_audit("team_blueprint_instantiated", {"blueprint_id": blueprint_id, "team_id": instantiated.id})
    team_payload = instantiated.model_dump()
    definition_metadata = team_definition_metadata(instantiated)
    team_payload["definition_metadata"] = definition_metadata
    team_payload["user_lifecycle_state"] = _user_lifecycle_state_from_metadata(definition_metadata)
    return api_response(data={"team": team_payload, "blueprint": _serialize_blueprint(blueprint)}, code=201)
