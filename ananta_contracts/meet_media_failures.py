"""Closed content-free capability failures, bound to the exact dispatched bytes."""

import hashlib
import hmac
import json

SCHEMA = "ananta.meet-media-failure.v1"
CODES = (
    "meet_video_encoder_unavailable_or_failed",
    "meet_video_encoder_timeout",
    "meet_piper_cuda_unavailable",
    "meet_piper_cuda_fallback_forbidden",
    "meet_piper_cuda_budget_unavailable",
)


class MediaCapabilityError(ValueError):
    def __init__(self, code):
        if code not in CODES:
            raise ValueError("meet_media_failure_code_invalid")
        super().__init__(code)


def failure_exit(error):
    return 80 + CODES.index(str(error)) if isinstance(error, MediaCapabilityError) else 1


def failure_from_exit(code):
    return CODES[code - 80] if type(code) is int and 80 <= code < 80 + len(CODES) else None


def failure_reply(turn, code):
    if code not in CODES:
        raise ValueError("meet_media_failure_code_invalid")
    return {"schema": SCHEMA, "task_id": turn["task_id"], "lease_id": turn["lease_id"], "error": {"code": code}}


def failure_signature(key, request_body, response_body):
    message = b"meet-media-failure-v1\0" + hashlib.sha256(request_body).digest() + response_body
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_failure(key, request_body, response_body, supplied):
    if type(response_body) is not bytes or len(response_body) > 1024:
        raise ValueError("meet_media_failure_invalid")
    if not hmac.compare_digest(failure_signature(key, request_body, response_body), supplied):
        raise ValueError("meet_media_failure_unauthorized")

    def closed_pairs(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("meet_media_failure_invalid")
            value[name] = item
        return value

    reply = json.loads(response_body, object_pairs_hook=closed_pairs)
    turn = json.loads(request_body)
    if not isinstance(reply, dict) or not isinstance(reply.get("error"), dict):
        raise ValueError("meet_media_failure_invalid")
    code = reply["error"].get("code")
    if reply != failure_reply(turn, code):
        raise ValueError("meet_media_failure_mismatch")
    return code
