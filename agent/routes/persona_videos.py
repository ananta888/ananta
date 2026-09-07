"""Authenticated video asset API; no raw clip download or publication endpoint."""

import base64
import re

from flask import Blueprint, Response, jsonify

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.models.persona_asset_policy import PersonaVideoPolicy
from agent.routes.persona_media_http import payload, revision, service
from ananta_contracts.persona_video import MAX_INPUT_BYTES, MAX_REQUEST_BYTES

persona_videos_bp = Blueprint("persona_videos", __name__)


@persona_videos_bp.put("/projects/<project>/video-policy")
@check_user_auth
def install_policy(project):
    policy_service = service("persona_video_policy")
    body = payload({"policy", "expected_revision"})
    policy = PersonaVideoPolicy.model_validate(body["policy"])
    if policy.project_id != project:
        raise PermissionError("persona_project_mismatch")
    policy_service.install(
        get_authenticated_source_control_principal(),
        policy,
        expected_revision=revision(body["expected_revision"], allow_zero=True),
    )
    return jsonify({"revision": policy.revision})


@persona_videos_bp.delete("/projects/<project>/video-policy/<source_id>")
@check_user_auth
def revoke_policy(project, source_id):
    body = payload({"expected_revision"})
    value = service("persona_video_policy").revoke_policy(
        get_authenticated_source_control_principal(),
        project,
        source_id,
        expected_revision=revision(body["expected_revision"]),
    )
    return jsonify({"revision": value, "state": "revoked"})


@persona_videos_bp.post("/projects/<project>/videos")
@check_user_auth
def admit_video(project):
    assets = service("persona_video_assets")
    body = payload(
        {"content", "media_type", "origin_binding", "license_binding", "consent_binding"}, maximum=MAX_REQUEST_BYTES
    )
    if (
        not isinstance(body["content"], str)
        or not 1 <= len(body["content"]) <= 4 * ((MAX_INPUT_BYTES + 2) // 3)
        or body["media_type"] != "video/mp4"
    ):
        raise ValueError("persona_video_input_invalid")
    for field in ("origin_binding", "license_binding", "consent_binding"):
        value = body[field]
        if field == "consent_binding" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"SRC_[A-Za-z0-9_.:-]{1,156}", value):
            raise ValueError("persona_video_provenance_invalid")
    content = base64.b64decode(body.pop("content"), validate=True)
    if not 0 < len(content) <= MAX_INPUT_BYTES:
        raise ValueError("persona_video_input_invalid")
    asset = assets.admit_video(get_authenticated_source_control_principal(), project, content=content, **body)
    return jsonify({"asset": asset.model_dump(mode="json"), "revision": 2, "state": "active"}), 201


@persona_videos_bp.get("/projects/<project>/videos/<artifact_id>/preview")
@check_user_auth
def preview(project, artifact_id):
    content = service("persona_video_assets").read_video(
        get_authenticated_source_control_principal(), project, artifact_id, purpose="preview"
    )
    return Response(content, mimetype="image/png")


@persona_videos_bp.get("/projects/<project>/videos/<artifact_id>/reference")
@check_user_auth
def reference(project, artifact_id):
    value = service("persona_profile_videos").reference(
        get_authenticated_source_control_principal(), project, artifact_id
    )
    return jsonify({"reference": value.model_dump(mode="json")})


@persona_videos_bp.delete("/projects/<project>/videos/<artifact_id>")
@check_user_auth
def revoke_video(project, artifact_id):
    body = payload({"expected_revision"})
    value = service("persona_video_assets").revoke(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        expected_revision=revision(body["expected_revision"]),
    )
    return jsonify({"revision": value, "state": "revoked"})


@persona_videos_bp.post("/projects/<project>/videos/<artifact_id>/purge")
@check_user_auth
def purge(project, artifact_id):
    body = payload({"expected_revision"})
    value = service("persona_video_erasure").purge(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        expected_revision=revision(body["expected_revision"]),
    )
    return jsonify({"revision": value, "state": "purged", "secure_device_erasure": False})


@persona_videos_bp.get("/projects/<project>/videos/<artifact_id>/purge")
@check_user_auth
def purge_status(project, artifact_id):
    return jsonify(
        service("persona_video_erasure").status(get_authenticated_source_control_principal(), project, artifact_id)
    )
