"""Headless generated-source ingress, separate from publication policy."""

import base64

from flask import Blueprint, jsonify

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.models.persona_generated_source import PersonaGeneratedOutput, PersonaGenerationRunPin
from agent.routes.persona_media_http import payload, service

persona_generated_sources_bp = Blueprint("persona_generated_sources", __name__)


@persona_generated_sources_bp.post("/projects/<project>/generated-sources")
@check_user_auth
def admit_generated_source(project):
    admission = service("persona_generated_sources")
    body = payload({"output", "run_pin", "content"}, maximum=7 * 1024 * 1024)
    output = PersonaGeneratedOutput.model_validate(body["output"])
    pin = PersonaGenerationRunPin.model_validate(body["run_pin"])
    if type(body["content"]) is not str or len(body["content"]) != 4 * ((output.content_size + 2) // 3):
        raise ValueError("persona_generation_content_invalid")
    source = admission.admit(
        get_authenticated_source_control_principal(),
        project,
        output=output,
        run_pin=pin,
        content=base64.b64decode(body["content"], validate=True),
    )
    return jsonify({"source": source.model_dump(mode="json"), "publication_authorized": False}), 201
