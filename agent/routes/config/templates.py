from __future__ import annotations

import re

from flask import Blueprint, current_app, g, request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import RoleDB, TeamDB, TeamMemberDB, TeamTypeRoleLink, TemplateDB
from agent.models import TemplateCreateRequest
from agent.services.repository_registry import get_repository_registry
from agent.utils import validate_request

templates_bp = Blueprint("config_templates", __name__)

ALLOWED_TEMPLATE_VARIABLES = {
    "agent_name",
    "task_title",
    "task_description",
    "team_name",
    "role_name",
    "team_goal",
    "anforderungen",
    "funktion",
    "feature_name",
    "title",
    "description",
    "task",
    "endpoint_name",
    "beschreibung",
    "sprache",
    "api_details",
}


def _template_repo():
    return get_repository_registry().template_repo


def _get_template_allowlist() -> set:
    cfg = current_app.config.get("AGENT_CONFIG", {})
    allowlist_cfg = cfg.get("template_variables_allowlist")
    if isinstance(allowlist_cfg, list) and allowlist_cfg:
        return set(allowlist_cfg)
    return ALLOWED_TEMPLATE_VARIABLES


def _template_validation_config() -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    raw = cfg.get("template_variable_validation")
    if not isinstance(raw, dict):
        return {"strict": False}
    return {"strict": bool(raw.get("strict", False))}


def validate_template_variables(template_text: str) -> list[str]:
    if not template_text:
        return []
    found_vars = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", template_text)
    allowlist = _get_template_allowlist()
    return [value for value in found_vars if value not in allowlist]


def _template_warnings(prompt_template: str) -> list[dict]:
    unknown = validate_template_variables(prompt_template)
    if not unknown:
        return []
    return [
        {
            "type": "unknown_variables",
            "details": f"Unknown variables: {', '.join(unknown)}",
            "allowed": list(_get_template_allowlist()),
        }
    ]


def _template_strict_validation_error(prompt_template: str):
    warnings = _template_warnings(prompt_template)
    if not warnings or not _template_validation_config().get("strict"):
        return None
    warning = warnings[0]
    return api_response(
        status="error",
        message="unknown_template_variables",
        data={
            "warnings": warnings,
            "allowed": warning.get("allowed") or [],
            "unknown_variables": validate_template_variables(prompt_template),
        },
        code=400,
    )


@templates_bp.route("/templates", methods=["GET"])
@check_auth
def list_templates():
    templates = _template_repo().get_all()
    return api_response(data=[template.model_dump() for template in templates])


@templates_bp.route("/templates", methods=["POST"])
@admin_required
@validate_request(TemplateCreateRequest)
def create_template():
    data: TemplateCreateRequest = g.validated_data
    template_name = data.name.strip()
    if not template_name:
        return api_response(status="error", message="template_name_required", code=400)
    existing = _template_repo().get_by_name(template_name)
    if existing is not None:
        return api_response(status="error", message="template_name_exists", data={"name": template_name}, code=409)
    strict_error = _template_strict_validation_error(data.prompt_template)
    if strict_error is not None:
        return strict_error
    warnings = _template_warnings(data.prompt_template)
    new_template = TemplateDB(name=template_name, description=data.description, prompt_template=data.prompt_template)
    try:
        _template_repo().save(new_template)
    except IntegrityError:
        return api_response(status="error", message="template_name_exists", data={"name": template_name}, code=409)
    log_audit("template_created", {"template_id": new_template.id, "name": new_template.name})
    payload = new_template.model_dump()
    if warnings:
        payload["warnings"] = warnings
    return api_response(data=payload, code=201)


@templates_bp.route("/templates/<tpl_id>", methods=["PUT", "PATCH"])
@admin_required
def update_template(tpl_id):
    data = request.get_json() or {}
    template = _template_repo().get_by_id(tpl_id)
    if not template:
        return api_response(status="error", message="not_found", code=404)
    if "prompt_template" in data:
        strict_error = _template_strict_validation_error(data["prompt_template"])
        if strict_error is not None:
            return strict_error
    warnings = _template_warnings(data["prompt_template"]) if "prompt_template" in data else []
    if "prompt_template" in data:
        template.prompt_template = data["prompt_template"]
    if "name" in data:
        template_name = str(data["name"] or "").strip()
        if not template_name:
            return api_response(status="error", message="template_name_required", code=400)
        existing = _template_repo().get_by_name(template_name)
        if existing is not None and existing.id != tpl_id:
            return api_response(status="error", message="template_name_exists", data={"name": template_name}, code=409)
        template.name = template_name
    if "description" in data:
        template.description = data["description"]
    try:
        _template_repo().save(template)
    except IntegrityError:
        return api_response(status="error", message="template_name_exists", data={"name": template.name}, code=409)
    log_audit("template_updated", {"template_id": tpl_id, "name": template.name})
    payload = template.model_dump()
    if warnings:
        payload["warnings"] = warnings
    return api_response(data=payload)


@templates_bp.route("/templates/<tpl_id>", methods=["DELETE"])
@admin_required
def delete_template(tpl_id):
    try:
        with Session(engine) as session:
            template = session.get(TemplateDB, tpl_id)
            if not template:
                return api_response(status="error", message="not_found", code=404)
            roles = session.exec(select(RoleDB).where(RoleDB.default_template_id == tpl_id)).all()
            links = session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.template_id == tpl_id)).all()
            members = session.exec(select(TeamMemberDB).where(TeamMemberDB.custom_template_id == tpl_id)).all()
            teams = session.exec(select(TeamDB)).all()
            cleared = {"roles": [role.id for role in roles], "team_type_links": [link.role_id for link in links], "team_members": [member.id for member in members], "teams": []}
            for role in roles:
                role.default_template_id = None
                session.add(role)
            for link in links:
                link.template_id = None
                session.add(link)
            for member in members:
                member.custom_template_id = None
                session.add(member)
            for team in teams:
                if isinstance(team.role_templates, dict) and tpl_id in team.role_templates.values():
                    team.role_templates = {key: value for key, value in team.role_templates.items() if value != tpl_id}
                    cleared["teams"].append(team.id)
                    session.add(team)
            if any(cleared.values()):
                current_app.logger.warning(f"Template delete clearing references: {tpl_id} refs={cleared}")
            session.delete(template)
            session.commit()
            log_audit("template_deleted", {"template_id": tpl_id, "cleared_refs": cleared})
            return api_response(data={"status": "deleted", "cleared": cleared})
    except Exception as exc:
        current_app.logger.exception(f"Template delete failed for {tpl_id}: {exc}")
        return api_response(status="error", message="delete_failed", data={"details": "Template delete failed"}, code=500)
