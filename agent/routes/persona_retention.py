"""Passive headless administration of explicit exact-image retention grants."""

from flask import Blueprint, jsonify

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.routes.persona_media_http import payload, service

persona_retention_bp = Blueprint("persona_retention", __name__)


@persona_retention_bp.put("/projects/<project>/images/<artifact_id>/retention")
@check_user_auth
def schedule(project, artifact_id):
    body = payload({"asset_revision", "expected_revision", "delete_after_seconds"}, maximum=512)
    return jsonify(
        service("persona_retention").schedule(
            get_authenticated_source_control_principal(),
            project,
            artifact_id,
            **body,
        )
    )


@persona_retention_bp.get("/projects/<project>/images/<artifact_id>/retention")
@check_user_auth
def status(project, artifact_id):
    return jsonify(
        service("persona_retention").status(
            get_authenticated_source_control_principal(),
            project,
            artifact_id,
        )
    )


@persona_retention_bp.delete("/projects/<project>/images/<artifact_id>/retention")
@check_user_auth
def cancel(project, artifact_id):
    body = payload({"expected_revision"}, maximum=512)
    revision = service("persona_retention").cancel(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        **body,
    )
    return jsonify({"revision": revision, "state": "cancelled"})
