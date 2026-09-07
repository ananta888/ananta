"""HMAC-only current voice task lease; no user bearer or image authority fallback."""

from flask import Blueprint, current_app, request

from agent.routes.persona_inspection_lease_response import inspection_lease_response
from agent.routes.persona_media_http import service
from ananta_contracts.persona_voice_wire import validate_assignment

persona_voice_lease_bp = Blueprint("persona_voice_lease", __name__)


@persona_voice_lease_bp.post("/internal/voice-lease")
def voice_lease():
    if request.headers.get("Authorization") is not None:
        raise PermissionError("persona_lease_user_bearer_denied")
    return inspection_lease_response(
        service=service("persona_voice_leases"),
        key=current_app.extensions.get("persona_voice_worker_key"),
        domain=b"persona-voice-lease-v1",
        validate_assignment=validate_assignment,
    )
