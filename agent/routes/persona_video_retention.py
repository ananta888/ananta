"""Headless administration of explicit video-only retirement grants."""

from flask import Blueprint, jsonify

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.routes.persona_media_http import payload, service

persona_video_retention_bp = Blueprint("persona_video_retention", __name__)


@persona_video_retention_bp.put("/projects/<project>/videos/<artifact_id>/retention")
@check_user_auth
def schedule(project, artifact_id):
    body = payload({"asset_revision", "expected_revision", "delete_after_seconds"}, maximum=512)
    return jsonify(
        service("persona_video_retention").schedule(
            get_authenticated_source_control_principal(),
            project,
            artifact_id,
            **body,
        )
    )


@persona_video_retention_bp.get("/projects/<project>/videos/<artifact_id>/retention")
@check_user_auth
def status(project, artifact_id):
    return jsonify(
        service("persona_video_retention").status(
            get_authenticated_source_control_principal(),
            project,
            artifact_id,
        )
    )


@persona_video_retention_bp.delete("/projects/<project>/videos/<artifact_id>/retention")
@check_user_auth
def cancel(project, artifact_id):
    body = payload({"expected_revision"}, maximum=512)
    revision = service("persona_video_retention").cancel(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        **body,
    )
    return jsonify({"revision": revision, "state": "cancelled"})
