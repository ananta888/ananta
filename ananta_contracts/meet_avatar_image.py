"""Closed avatar image hydration envelope; the small dialog API stays unchanged."""

import hashlib
import hmac
import json
import re

from ananta_contracts.meet_dialog import ID, MAX_DIALOG_BYTES
from ananta_contracts.meet_persona_image import decode_assignment, validate_reference

REQUEST_SCHEMA = "ananta.meet-avatar-image-request.v1"
RESPONSE_SCHEMA = "ananta.meet-avatar-image-response.v1"
MAX_AVATAR_IMAGE_BYTES = 8 * 1024 * 1024
_IDS = frozenset({"tenant_id", "project_id", "task_id", "lease_id", "runtime_id", "session_id", "own_peer_id"})
_COUNTERS = frozenset({"generation", "membership_epoch", "avatar_revision", "deadline_ms"})


def validate_image_binding(value):
    if (
        not isinstance(value, dict)
        or set(value) != _IDS | _COUNTERS | {"meet_session_id", "room_id", "selection_digest"}
        or any(not isinstance(value[k], str) or not ID.fullmatch(value[k]) for k in _IDS)
        or any(type(value[k]) is not int or not 1 <= value[k] < 2**53 for k in _COUNTERS)
        or value["avatar_revision"] > 1023
        or not isinstance(value["meet_session_id"], str)
        or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", value["meet_session_id"])
        or not isinstance(value["room_id"], str)
        or not re.fullmatch(r"room-[a-f0-9]{18}", value["room_id"])
        or not isinstance(value["selection_digest"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["selection_digest"])
    ):
        raise ValueError("meet_avatar_image_binding_invalid")
    return value


def validate_image_request(value, now):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "sent_at", "binding"}
        or value["schema"] != REQUEST_SCHEMA
        or not isinstance(value["nonce"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
        or type(value["sent_at"]) is not int
        or not now - 10 <= value["sent_at"] <= now + 2
    ):
        raise ValueError("meet_avatar_image_request_invalid")
    binding = validate_image_binding(value["binding"])
    if not now * 1000 < binding["deadline_ms"] <= (now + 7200) * 1000:
        raise ValueError("meet_avatar_image_request_expired")
    return value


def decode_image_response(value, request, reference, now_ms):
    if (
        type(now_ms) is not int
        or not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "binding", "image"}
        or value["schema"] != RESPONSE_SCHEMA
        or value["nonce"] != request["nonce"]
        or validate_image_binding(value["binding"]) != validate_image_binding(request["binding"])
        or not 0 < now_ms < value["binding"]["deadline_ms"]
    ):
        raise ValueError("meet_avatar_image_response_invalid")
    image = value["image"]
    if not isinstance(image, dict) or image.get("reference") != validate_reference(reference):
        raise ValueError("meet_avatar_image_reference_changed")
    binding = value["binding"]
    decode_assignment(image, tenant_id=binding["tenant_id"], project_id=binding["project_id"])
    return image


def parse_image_message(raw, *, response=False):
    maximum = MAX_AVATAR_IMAGE_BYTES if response else MAX_DIALOG_BYTES
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError("meet_avatar_image_size_invalid")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("meet_avatar_image_duplicate_field")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("meet_avatar_image_number_invalid")

    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, RecursionError):
        raise ValueError("meet_avatar_image_json_invalid") from None


def image_request_signature(key, body):
    return hmac.new(key, b"meet-avatar-image-request-v1\0" + body, hashlib.sha256).hexdigest()


def image_response_signature(key, request_body, response_body):
    return hmac.new(
        key, b"meet-avatar-image-response-v1\0" + hashlib.sha256(request_body).digest() + response_body, hashlib.sha256
    ).hexdigest()
