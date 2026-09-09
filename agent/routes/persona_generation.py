"""Explicit headless creation and a separate read-only generation lease."""

from flask import Blueprint, current_app, jsonify

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.models.persona_generation import PersonaGenerationRequest
from agent.routes.persona_inspection_lease_response import inspection_lease_response
from agent.routes.persona_media_http import payload, service
from ananta_contracts.persona_generation import validate_assignment

persona_generation_bp = Blueprint("persona_generation", __name__)


@persona_generation_bp.post("/projects/<project>/generated-assets")
@check_user_auth
def create_generated_asset(project):
    creator = service("persona_generated_assets")
    request = PersonaGenerationRequest.model_validate(payload({"request"})["request"])
    asset = creator.create(get_authenticated_source_control_principal(), project, request)
    return jsonify({"asset": asset.model_dump(mode="json"), "revision": 2, "state": "active"}), 201


@persona_generation_bp.post("/internal/generation-lease")
def generation_lease():
    return inspection_lease_response(
        service=service("persona_generation_leases"),
        key=current_app.extensions.get("persona_generation_worker_key"),
        domain=b"persona-generation-lease-v1",
        validate_assignment=validate_assignment,
    )
