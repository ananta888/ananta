from __future__ import annotations

from flask import Blueprint, current_app, g, request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import RoleDB, TeamDB, TeamMemberDB, TeamTypeRoleLink, TemplateDB
from agent.models import TemplateCreateRequest, TemplatePreviewRequest, TemplateValidationRequest
from agent.services.repository_registry import get_repository_registry
from agent.services.template_variable_registry import (
    build_template_sample_contexts_payload,
    build_template_validation_diagnostics,
    build_template_runtime_contract_payload,
    build_template_variable_registry_payload,
    normalize_template_context_scope,
    render_template_preview,
    resolve_template_sample_context,
    resolve_template_validation_context,
    resolve_allowed_template_variables,
    validate_template_variables_with_context,
)
from agent.services.team_definition_version_service import serialize_template_with_version
from agent.utils import validate_request

templates_bp = Blueprint("config_templates", __name__)


def _template_repo():
    return get_repository_registry().template_repo


def _get_template_allowlist() -> set:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return set(resolve_allowed_template_variables(cfg))


def _template_validation_config() -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    raw = cfg.get("template_variable_validation")
    if not isinstance(raw, dict):
        return {"strict": False, "context_scope": None}
    return {
        "strict": bool(raw.get("strict", False)),
        "context_scope": normalize_template_context_scope(raw.get("context_scope")),
    }


def validate_template_variables(template_text: str, *, context_scope: str | None = None) -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return validate_template_variables_with_context(
        template_text,
        context_scope=context_scope,
        agent_cfg=cfg,
    )


def _template_validation_result(prompt_template: str, context_scope: str | None = None) -> dict:
    cfg = _template_validation_config()
    resolved_scope = resolve_template_validation_context(
        request_scope=context_scope,
        config_scope=cfg.get("context_scope"),
    )
    return validate_template_variables(prompt_template, context_scope=resolved_scope)


def _template_warnings(prompt_template: str, context_scope: str | None = None) -> tuple[list[dict], dict]:
    validation = _template_validation_result(prompt_template, context_scope=context_scope)
    warnings: list[dict] = []
    if validation.get("unknown_variables"):
        warnings.append(
            {
                "type": "unknown_variables",
                "details": f"Unknown variables: {', '.join(validation['unknown_variables'])}",
                "allowed": sorted(_get_template_allowlist()),
            }
        )
    if validation.get("context_invalid_variables"):
        context_label = validation.get("context_scope") or "selected scope"
        warnings.append(
            {
                "type": "context_unavailable_variables",
                "details": (
                    f"Unavailable in context '{context_label}': "
                    f"{', '.join(validation['context_invalid_variables'])}"
                ),
                "context_scope": context_label,
                "allowed_for_context": validation.get("available_variables_for_context") or [],
            }
        )
    if validation.get("deprecated_variables"):
        warnings.append(
            {
                "type": "deprecated_variables",
                "details": (
                    "Deprecated legacy variables: "
                    f"{', '.join(validation['deprecated_variables'])}"
                ),
                "deprecated_variables": validation["deprecated_variables"],
            }
        )
    return warnings, validation


def _template_strict_validation_error(prompt_template: str, context_scope: str | None = None):
    validation_cfg = _template_validation_config()
    warnings, validation = _template_warnings(prompt_template, context_scope=context_scope)
    if not validation_cfg.get("strict"):
        return None
    unknown = validation.get("unknown_variables") or []
    context_invalid = validation.get("context_invalid_variables") or []
    if not unknown and not context_invalid:
        return None
    message = "template_validation_failed"
    if unknown and not context_invalid:
        message = "unknown_template_variables"
    if context_invalid and not unknown:
        message = "context_unavailable_template_variables"
    diagnostics = build_template_validation_diagnostics(
        prompt_template,
        context_scope=validation.get("context_scope"),
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
    )
    return api_response(
        status="error",
        message=message,
        data={
            "warnings": warnings,
            "allowed": sorted(_get_template_allowlist()),
            "unknown_variables": unknown,
            "context_invalid_variables": context_invalid,
            "deprecated_variables": validation.get("deprecated_variables") or [],
            "context_scope": validation.get("context_scope"),
            "diagnostics": diagnostics,
        },
        code=400,
    )


@templates_bp.route("/templates/variable-registry", methods=["GET"])
@check_auth
def template_variable_registry_read_model():
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return api_response(data=build_template_variable_registry_payload(agent_cfg=cfg))


@templates_bp.route("/templates/runtime-contract", methods=["GET"])
@check_auth
def template_runtime_contract_read_model():
    return api_response(data=build_template_runtime_contract_payload())


@templates_bp.route("/templates/sample-contexts", methods=["GET"])
@check_auth
def template_sample_contexts_read_model():
    return api_response(data=build_template_sample_contexts_payload())


@templates_bp.route("/templates/validate", methods=["POST"])
@check_auth
@validate_request(TemplateValidationRequest)
def validate_template_payload():
    data: TemplateValidationRequest = g.validated_data
    validation = _template_validation_result(
        data.prompt_template,
        context_scope=data.context_scope,
    )
    warnings, _ = _template_warnings(
        data.prompt_template,
        context_scope=data.context_scope,
    )
    diagnostics = build_template_validation_diagnostics(
        data.prompt_template,
        context_scope=validation.get("context_scope"),
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
    )
    return api_response(
        data={
            **validation,
            "warnings": warnings,
            "is_valid": not validation.get("unknown_variables")
            and not validation.get("context_invalid_variables"),
            "diagnostics": diagnostics,
        }
    )


@templates_bp.route("/templates/preview", methods=["POST"])
@check_auth
@validate_request(TemplatePreviewRequest)
def preview_template_payload():
    data: TemplatePreviewRequest = g.validated_data
    sample_name, sample_context = resolve_template_sample_context(
        context_scope=data.context_scope,
        sample_context=data.sample_context,
        context_payload=data.context_payload,
    )
    validation = _template_validation_result(
        data.prompt_template,
        context_scope=data.context_scope,
    )
    preview = render_template_preview(data.prompt_template, context_payload=sample_context)
    diagnostics = build_template_validation_diagnostics(
        data.prompt_template,
        context_scope=validation.get("context_scope"),
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        preview_context=sample_context,
    )
    return api_response(
        data={
            "context_scope": validation.get("context_scope"),
            "sample_context": sample_name,
            "sample_context_keys": sorted(list(sample_context.keys())),
            "preview": preview,
            "validation": validation,
            "diagnostics": diagnostics,
        }
    )


@templates_bp.route("/templates/validation-diagnostics", methods=["POST"])
@check_auth
@validate_request(TemplatePreviewRequest)
def template_validation_diagnostics():
    data: TemplatePreviewRequest = g.validated_data
    sample_name, sample_context = resolve_template_sample_context(
        context_scope=data.context_scope,
        sample_context=data.sample_context,
        context_payload=data.context_payload,
    )
    validation = _template_validation_result(
        data.prompt_template,
        context_scope=data.context_scope,
    )
    diagnostics = build_template_validation_diagnostics(
        data.prompt_template,
        context_scope=validation.get("context_scope"),
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        preview_context=sample_context,
    )
    log_audit(
        "template_validation_diagnostics_requested",
        {
            "context_scope": validation.get("context_scope"),
            "sample_context": sample_name,
            "issue_count": diagnostics.get("issue_count"),
            "severity": diagnostics.get("severity"),
        },
    )
    return api_response(
        data={
            "context_scope": validation.get("context_scope"),
            "sample_context": sample_name,
            "diagnostics": diagnostics,
            "summary": validation.get("summary") or {},
        }
    )


@templates_bp.route("/templates", methods=["GET"])
@check_auth
def list_templates():
    templates = _template_repo().get_all()
    return api_response(data=[serialize_template_with_version(template) for template in templates])


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
    strict_error = _template_strict_validation_error(
        data.prompt_template,
        context_scope=data.validation_context,
    )
    if strict_error is not None:
        return strict_error
    warnings, validation = _template_warnings(
        data.prompt_template,
        context_scope=data.validation_context,
    )
    new_template = TemplateDB(name=template_name, description=data.description, prompt_template=data.prompt_template)
    try:
        _template_repo().save(new_template)
    except IntegrityError:
        return api_response(status="error", message="template_name_exists", data={"name": template_name}, code=409)
    log_audit("template_created", {"template_id": new_template.id, "name": new_template.name})
    payload = serialize_template_with_version(new_template)
    if warnings:
        payload["warnings"] = warnings
    payload["validation_summary"] = validation.get("summary") or {}
    payload["validation_context_scope"] = validation.get("context_scope")
    return api_response(data=payload, code=201)


@templates_bp.route("/templates/<tpl_id>", methods=["PUT", "PATCH"])
@admin_required
def update_template(tpl_id):
    data = request.get_json() or {}
    template = _template_repo().get_by_id(tpl_id)
    if not template:
        return api_response(status="error", message="not_found", code=404)
    request_context = data.get("validation_context")
    if "prompt_template" in data:
        strict_error = _template_strict_validation_error(
            data["prompt_template"],
            context_scope=request_context,
        )
        if strict_error is not None:
            return strict_error
    warnings: list[dict] = []
    validation: dict | None = None
    if "prompt_template" in data:
        warnings, validation = _template_warnings(
            data["prompt_template"],
            context_scope=request_context,
        )
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
    payload = serialize_template_with_version(template)
    if warnings:
        payload["warnings"] = warnings
    if validation is not None:
        payload["validation_summary"] = validation.get("summary") or {}
        payload["validation_context_scope"] = validation.get("context_scope")
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
