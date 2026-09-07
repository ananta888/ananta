"""Authenticated voice descriptor metadata API; never synthesizes or publishes audio."""

import base64
import re

from flask import Blueprint, Response, jsonify

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.models.persona_asset_policy import PersonaVoicePolicy
from agent.routes.persona_media_http import payload, revision, service
from ananta_contracts.persona_voice import MAX_DESCRIPTOR_BYTES, MEDIA_TYPE

persona_voices_bp = Blueprint("persona_voices", __name__)


@persona_voices_bp.put("/projects/<project>/voice-policy")
@check_user_auth
def install_policy(project):
    policy_service = service("persona_voice_policy")
    body = payload({"policy", "expected_revision"})
    policy = PersonaVoicePolicy.model_validate(body["policy"])
    if policy.project_id != project:
        raise PermissionError("persona_project_mismatch")
    policy_service.install(
        get_authenticated_source_control_principal(),
        policy,
        expected_revision=revision(body["expected_revision"], allow_zero=True),
    )
    return jsonify({"revision": policy.revision})


@persona_voices_bp.delete("/projects/<project>/voice-policy/<source_id>")
@check_user_auth
def revoke_policy(project, source_id):
    body = payload({"expected_revision"})
    value = service("persona_voice_policy").revoke_policy(
        get_authenticated_source_control_principal(),
        project,
        source_id,
        expected_revision=revision(body["expected_revision"]),
    )
    return jsonify({"revision": value, "state": "revoked"})


@persona_voices_bp.post("/projects/<project>/voices")
@check_user_auth
def admit_voice(project):
    assets = service("persona_voice_assets")
    body = payload({"content", "media_type", "origin_binding", "license_binding", "consent_binding"}, maximum=4096)
    if (
        type(body["content"]) is not str
        or not 0 < len(body["content"]) <= 4 * ((MAX_DESCRIPTOR_BYTES + 2) // 3)
        or body["media_type"] != MEDIA_TYPE
    ):
        raise ValueError("persona_voice_input_invalid")
    for field in ("origin_binding", "license_binding", "consent_binding"):
        value = body[field]
        if field == "consent_binding" and value is None:
            continue
        if type(value) is not str or not re.fullmatch(r"SRC_[A-Za-z0-9_.:-]{1,156}", value):
            raise ValueError("persona_voice_provenance_invalid")
    content = base64.b64decode(body.pop("content"), validate=True)
    if not 0 < len(content) <= MAX_DESCRIPTOR_BYTES:
        raise ValueError("persona_voice_input_invalid")
    asset = assets.admit(get_authenticated_source_control_principal(), project, content=content, **body)
    return jsonify({"asset": asset.model_dump(mode="json"), "revision": 2, "state": "active"}), 201


@persona_voices_bp.post("/projects/<project>/voices/query")
@check_user_auth
def query_voices(project):
    body = payload({"cursor", "limit"}, maximum=512)
    return jsonify(service("persona_voice_query").query(get_authenticated_source_control_principal(), project, **body))


@persona_voices_bp.get("/projects/<project>/voices/<artifact_id>/preview")
@check_user_auth
def preview(project, artifact_id):
    content = service("persona_voice_assets").read(
        get_authenticated_source_control_principal(), project, artifact_id, purpose="preview"
    )
    return Response(content, mimetype=MEDIA_TYPE)


@persona_voices_bp.get("/projects/<project>/voices/<artifact_id>/reference")
@check_user_auth
def reference(project, artifact_id):
    value = service("persona_profile_voices").reference(
        get_authenticated_source_control_principal(), project, artifact_id
    )
    return jsonify({"reference": value.model_dump(mode="json")})


@persona_voices_bp.delete("/projects/<project>/voices/<artifact_id>")
@check_user_auth
def revoke_voice(project, artifact_id):
    body = payload({"expected_revision"})
    value = service("persona_voice_assets").revoke(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        expected_revision=revision(body["expected_revision"]),
    )
    return jsonify({"revision": value, "state": "revoked"})


@persona_voices_bp.post("/projects/<project>/voices/<artifact_id>/purge")
@check_user_auth
def purge(project, artifact_id):
    body = payload({"expected_revision"})
    value = service("persona_voice_erasure").purge(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        expected_revision=revision(body["expected_revision"]),
    )
    return jsonify({"revision": value, "state": "purged", "secure_device_erasure": False})


@persona_voices_bp.get("/projects/<project>/voices/<artifact_id>/purge")
@check_user_auth
def purge_status(project, artifact_id):
    return jsonify(
        service("persona_voice_erasure").status(get_authenticated_source_control_principal(), project, artifact_id)
    )
