import logging
import re
from flask import Blueprint, current_app, request
from sqlmodel import Session, select
from agent.auth import check_auth, admin_required
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import TemplateDB
from agent.models import TemplateCreateRequest
from agent.utils import validate_request

templates_bp = Blueprint("config_templates", __name__)

ALLOWED_TEMPLATE_VARIABLES = {
    "agent_name", "task_title", "task_description", "team_name", "role_name",
    "team_goal", "anforderungen", "funktion", "feature_name", "title",
    "description", "task", "endpoint_name", "beschreibung", "sprache", "api_details"
}

def _get_template_allowlist() -> set:
    cfg = current_app.config.get("AGENT_CONFIG", {})
    allowlist_cfg = cfg.get("template_variables_allowlist")
    if isinstance(allowlist_cfg, list) and allowlist_cfg:
        return set(allowlist_cfg)
    return ALLOWED_TEMPLATE_VARIABLES

def validate_template_variables(template_text: str) -> list[str]:
    if not template_text:
        return []
    found_vars = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", template_text)
    allowlist = _get_template_allowlist()
    unknown_vars = [v for v in found_vars if v not in allowlist]
    return unknown_vars

@templates_bp.route("/templates", methods=["GET"])
def list_templates():
    check_auth()
    with Session(engine) as session:
        templates = session.exec(select(TemplateDB)).all()
        return api_response(data={"templates": [t.dict() for t in templates]})

@templates_bp.route("/templates", methods=["POST"])
@validate_request(TemplateCreateRequest)
def create_template():
    admin_required()
    data = request.get_json()
    name = data.get("name")
    prompt_tpl = data.get("prompt_template")

    if not name or not prompt_tpl:
        return api_response(status="error", message="Name und Prompt-Template erforderlich", code=400)

    unknown = validate_template_variables(prompt_tpl)
    if unknown:
        return api_response(
            status="error",
            message=f"Unbekannte Variablen im Template: {', '.join(unknown)}",
            code=400
        )

    with Session(engine) as session:
        new_tpl = TemplateDB(
            name=name,
            description=data.get("description"),
            prompt_template=prompt_tpl
        )
        session.add(new_tpl)
        session.commit()
        session.refresh(new_tpl)
        return api_response(data=new_tpl.dict(), message="Template erstellt")

@templates_bp.route("/templates/<int:tpl_id>", methods=["PUT", "PATCH"])
def update_template(tpl_id):
    admin_required()
    data = request.get_json()
    prompt_tpl = data.get("prompt_template")

    if prompt_tpl:
        unknown = validate_template_variables(prompt_tpl)
        if unknown:
            return api_response(
                status="error",
                message=f"Unbekannte Variablen im Template: {', '.join(unknown)}",
                code=400
            )

    with Session(engine) as session:
        tpl = session.get(TemplateDB, tpl_id)
        if not tpl:
            return api_response(status="error", message="Template nicht gefunden", code=404)

        if "name" in data:
            tpl.name = data["name"]
        if "description" in data:
            tpl.description = data["description"]
        if "prompt_template" in data:
            tpl.prompt_template = data["prompt_template"]

        session.add(tpl)
        session.commit()
        session.refresh(tpl)
        return api_response(data=tpl.dict(), message="Template aktualisiert")

@templates_bp.route("/templates/<int:tpl_id>", methods=["DELETE"])
def delete_template(tpl_id):
    admin_required()
    with Session(engine) as session:
        tpl = session.get(TemplateDB, tpl_id)
        if not tpl:
            return api_response(status="error", message="Template nicht gefunden", code=404)
        session.delete(tpl)
        session.commit()
        return api_response(message="Template gelöscht")
