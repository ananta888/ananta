"""Opted-in video artwork envelope; legacy image authority stays image-only."""

import hashlib
import hmac

from ananta_contracts.meet_avatar_image import (
    validate_avatar_projection,
    validate_image_binding,
    validate_image_request,
)
from ananta_contracts.meet_persona_video import decode_assignment, validate_reference

REQUEST_SCHEMA = "ananta.meet-avatar-video-request.v1"
RESPONSE_SCHEMA = "ananta.meet-avatar-video-response.v1"
MAX_AVATAR_VIDEO_BYTES = 2_600_000


def require_video_probe(value):
    if (
        type(value) is not dict
        or set(value) != {"schema", "profile", "mp4H264"}
        or value["schema"] != "ananta.meet-avatar-video-probe.v1"
        or value["profile"] != "persona-video-v1"
        or value["mp4H264"] is not True
    ):
        raise ValueError("meet_avatar_video_client_unsupported")


def validate_video_projection(value):
    if not isinstance(value, dict) or value.get("mode") != "persona-video-v1":
        return validate_avatar_projection(value)
    if (
        set(value) != {"mode", "state", "binding", "reference", "repeat_mode"}
        or value["state"] not in ("ready", "paused", "blocked")
        or value["repeat_mode"] not in ("loop", "hold_last")
    ):
        raise ValueError("meet_avatar_video_projection_invalid")
    if value["state"] == "ready":
        binding, reference = validate_image_binding(value["binding"]), validate_reference(value["reference"])
        if any(reference[name] != binding[name] for name in ("tenant_id", "project_id")):
            raise ValueError("meet_avatar_video_projection_invalid")
    elif value["binding"] is not None or value["reference"] is not None:
        raise ValueError("meet_avatar_video_projection_invalid")
    return value


def validate_video_request(value, now):
    if not isinstance(value, dict) or value.get("schema") != REQUEST_SCHEMA:
        raise ValueError("meet_avatar_video_request_invalid")
    # Reuse the exact freshness and immutable source-generation binding checks,
    # never the image transport domain or its content permission.
    validate_image_request(value | {"schema": "ananta.meet-avatar-image-request.v1"}, now)
    return value


def decode_video_response(value, request, reference, repeat_mode, now_ms):
    if (
        type(now_ms) is not int
        or not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "binding", "video"}
        or value["schema"] != RESPONSE_SCHEMA
        or value["nonce"] != request["nonce"]
        or validate_image_binding(value["binding"]) != validate_image_binding(request["binding"])
        or not 0 < now_ms < value["binding"]["deadline_ms"]
        or repeat_mode not in ("loop", "hold_last")
    ):
        raise ValueError("meet_avatar_video_response_invalid")
    video, binding = value["video"], value["binding"]
    if (
        not isinstance(video, dict)
        or video.get("reference") != validate_reference(reference)
        or video.get("repeat_mode") != repeat_mode
    ):
        raise ValueError("meet_avatar_video_reference_changed")
    decode_assignment(video, tenant_id=binding["tenant_id"], project_id=binding["project_id"])
    return video


def video_request_signature(key, body):
    return hmac.new(key, b"meet-avatar-video-request-v1\0" + body, hashlib.sha256).hexdigest()


def video_response_signature(key, request_body, response_body):
    return hmac.new(
        key, b"meet-avatar-video-response-v1\0" + hashlib.sha256(request_body).digest() + response_body, hashlib.sha256
    ).hexdigest()
