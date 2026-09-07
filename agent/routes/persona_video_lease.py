"""Separate HMAC-only live video lease; no user bearer or image grant fallback."""

from flask import Blueprint, current_app, request

from agent.routes.persona_inspection_lease_response import inspection_lease_response
from agent.routes.persona_media_http import service
from ananta_contracts.persona_video import validate_assignment

persona_video_lease_bp = Blueprint("persona_video_lease", __name__)


@persona_video_lease_bp.post("/internal/video-lease")
def video_lease():
    if request.headers.get("Authorization") is not None:
        raise PermissionError("persona_lease_user_bearer_denied")
    return inspection_lease_response(
        service=service("persona_video_leases"),
        key=current_app.extensions.get("persona_video_worker_key"),
        domain=b"persona-video-lease-v1",
        validate_assignment=validate_assignment,
    )
