"""Headless user asset API and a separately authenticated read-only worker lease."""

import base64
import re

from flask import Blueprint, Response, current_app, jsonify, request

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.models.persona_asset_policy import PersonaImagePolicy
from agent.models.persona_media import PersonaMediaProfile
from agent.routes.persona_inspection_lease_response import inspection_lease_response
from agent.routes.persona_media_http import payload as _payload
from agent.routes.persona_media_http import revision as _revision
from agent.routes.persona_media_http import service as _service
from agent.routes.persona_retention import persona_retention_bp
from agent.routes.persona_video_lease import persona_video_lease_bp
from agent.routes.persona_video_retention import persona_video_retention_bp
from agent.routes.persona_videos import persona_videos_bp
from agent.routes.persona_voice_lease import persona_voice_lease_bp
from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.persona_image import MAX_REQUEST_BYTES, validate_assignment

persona_media_bp = Blueprint("persona_media", __name__, url_prefix="/api/persona-media/v1")
persona_media_bp.register_blueprint(persona_retention_bp)
persona_media_bp.register_blueprint(persona_video_lease_bp)
persona_media_bp.register_blueprint(persona_video_retention_bp)
persona_media_bp.register_blueprint(persona_videos_bp)
persona_media_bp.register_blueprint(persona_voice_lease_bp)


@persona_media_bp.before_request
def bounded_auth():
    header = request.headers.get("Authorization")
    if header is not None and (len(header) > 8192 or not re.fullmatch(r"Bearer [A-Za-z0-9._~-]+", header)):
        return jsonify({"error": {"code": "persona_auth_invalid"}}), 401
    if request.args or request.headers.get("Transfer-Encoding"):
        return jsonify({"error": {"code": "persona_request_invalid"}}), 400


@persona_media_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@persona_media_bp.errorhandler(ValueError)
def invalid(_error):
    return jsonify({"error": {"code": "persona_request_invalid_or_unavailable"}}), 409


@persona_media_bp.errorhandler(PermissionError)
def denied(_error):
    return jsonify({"error": {"code": "persona_access_denied"}}), 403


@persona_media_bp.errorhandler(ProjectAccessError)
def project_denied(error):
    return jsonify({"error": {"code": error.reason_code}}), error.public_status


@persona_media_bp.put("/projects/<project>/image-policy")
@check_user_auth
def install_policy(project):
    service = _service("persona_image_policy")
    payload = _payload({"policy", "expected_revision"})
    policy = PersonaImagePolicy.model_validate(payload["policy"])
    if policy.project_id != project:
        raise PermissionError("persona_project_mismatch")
    service.install(
        get_authenticated_source_control_principal(),
        policy,
        expected_revision=_revision(payload["expected_revision"], allow_zero=True),
    )
    return jsonify({"revision": policy.revision})


@persona_media_bp.delete("/projects/<project>/image-policy/<source_id>")
@check_user_auth
def revoke_policy(project, source_id):
    service = _service("persona_image_policy")
    payload = _payload({"expected_revision"})
    revision = service.revoke_policy(
        get_authenticated_source_control_principal(),
        project,
        source_id,
        expected_revision=_revision(payload["expected_revision"]),
    )
    return jsonify({"revision": revision, "state": "revoked"})


@persona_media_bp.post("/projects/<project>/images")
@check_user_auth
def admit_image(project):
    service = _service("persona_assets")
    payload = _payload(
        {"content", "media_type", "origin_binding", "license_binding", "consent_binding"},
        maximum=MAX_REQUEST_BYTES,
    )
    if not isinstance(payload["content"], str):
        raise ValueError("persona_content_invalid")
    for name in ("origin_binding", "license_binding", "consent_binding"):
        value = payload[name]
        if name == "consent_binding" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
            raise ValueError("persona_provenance_invalid")
    if payload["media_type"] not in ("image/png", "image/jpeg"):
        raise ValueError("persona_media_type_invalid")
    content = base64.b64decode(payload.pop("content"), validate=True)
    asset = service.admit_image(get_authenticated_source_control_principal(), project, content=content, **payload)
    return jsonify({"asset": asset.model_dump(mode="json"), "revision": 2, "state": "active"}), 201


@persona_media_bp.get("/projects/<project>/images/<artifact_id>/preview")
@check_user_auth
def preview(project, artifact_id):
    content = _service("persona_assets").read_image(
        get_authenticated_source_control_principal(), project, artifact_id, purpose="preview"
    )
    return Response(content, mimetype="image/png")


@persona_media_bp.delete("/projects/<project>/images/<artifact_id>")
@check_user_auth
def revoke_image(project, artifact_id):
    service = _service("persona_assets")
    payload = _payload({"expected_revision"})
    revision = service.revoke(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        expected_revision=_revision(payload["expected_revision"]),
    )
    return jsonify({"revision": revision, "state": "revoked"})


@persona_media_bp.post("/internal/image-lease")
def image_lease():
    return inspection_lease_response(
        service=_service("persona_image_leases"),
        key=current_app.extensions.get("persona_image_worker_key"),
        domain=b"persona-lease-v1",
        validate_assignment=validate_assignment,
    )


@persona_media_bp.post("/projects/<project>/images/<artifact_id>/purge")
@check_user_auth
def purge_image(project, artifact_id):
    service = _service("persona_asset_erasure")
    payload = _payload({"expected_revision"})
    revision = service.purge(
        get_authenticated_source_control_principal(),
        project,
        artifact_id,
        expected_revision=_revision(payload["expected_revision"]),
    )
    return jsonify({"revision": revision, "state": "purged", "secure_device_erasure": False})


@persona_media_bp.get("/projects/<project>/images/<artifact_id>/purge")
@check_user_auth
def purge_status(project, artifact_id):
    return jsonify(
        _service("persona_asset_erasure").status(get_authenticated_source_control_principal(), project, artifact_id)
    )


@persona_media_bp.get("/projects/<project>/images/<artifact_id>/reference")
@check_user_auth
def image_reference(project, artifact_id):
    reference = _service("persona_profile_images").reference(
        get_authenticated_source_control_principal(), project, artifact_id
    )
    return jsonify({"reference": reference.model_dump(mode="json")})


@persona_media_bp.post("/projects/<project>/images/query")
@check_user_auth
def image_query(project):
    payload = _payload({"cursor", "limit"}, maximum=512)
    return jsonify(
        _service("persona_image_query").query(
            get_authenticated_source_control_principal(),
            project,
            cursor=payload["cursor"],
            limit=payload["limit"],
        )
    )


@persona_media_bp.get("/projects/<project>/organizations/<organization>/profiles/<kind>/<owner>")
@check_user_auth
def current_profile(project, organization, kind, owner):
    return jsonify(
        _service("persona_profiles").current(
            get_authenticated_source_control_principal(), project, organization, kind, owner
        )
    )


@persona_media_bp.put("/projects/<project>/organizations/<organization>/profiles/<kind>/<owner>")
@check_user_auth
def save_profile(project, organization, kind, owner):
    payload = _payload({"profile", "expected_revision"})
    profile = PersonaMediaProfile.model_validate(payload["profile"])
    digest = _service("persona_profiles").save(
        get_authenticated_source_control_principal(),
        project,
        organization,
        kind,
        owner,
        profile,
        expected_revision=_revision(payload["expected_revision"], allow_zero=True),
    )
    return jsonify({"revision": profile.revision, "content_hash": digest})


@persona_media_bp.get("/projects/<project>/organizations/<organization>/profiles/<kind>/<owner>/effective")
@check_user_auth
def effective_profile(project, organization, kind, owner):
    return jsonify(
        _service("persona_profiles").effective(
            get_authenticated_source_control_principal(), project, organization, kind, owner
        )
    )
