"""Admin-only, bounded Hub grant issuance; Workers cannot request authority."""

from flask import Blueprint, request

from agent.auth import admin_required
from agent.common.errors import api_response
from agent.services.specialist_inference_grant_service import (
    GrantAuditUnavailable,
    get_specialist_inference_grant_service,
)

specialist_inference_bp = Blueprint("specialist_inference", __name__, url_prefix="/api/specialist-inference")


@specialist_inference_bp.post("/assignments")
@admin_required
def issue_assignment():
    if request.content_length is None or request.content_length > 32768:
        return api_response(status="error", message="specialist_grant_request_budget", code=413)
    try:
        service = get_specialist_inference_grant_service()
    except (ValueError, TypeError, OSError):
        return api_response(status="error", message="specialist_grant_unavailable", code=503)
    try:
        return api_response(data=service.issue(request.get_json(silent=True)))
    except (ValueError, TypeError, KeyError):
        return api_response(status="error", message="specialist_grant_binding_denied", code=422)
    except GrantAuditUnavailable:
        return api_response(status="error", message="specialist_grant_audit_unavailable", code=503)
