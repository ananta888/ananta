"""Closed Hub-to-worker contract shared without Flask or GPU dependencies."""

import hashlib
import hmac
import json
import re

SCHEMA = "ananta.meet-turn.v1"
MAX_REQUEST_BYTES = 7 * 1024 * 1024
MAX_RESULT_BYTES = 8_000_000
MAX_SECONDS = 120
FIELDS = {"schema", "task_id", "lease_id", "tenant_id", "project_id", "deadline", "text"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def signature(key, body):
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def validate_turn(value, now):
    if (
        not isinstance(value, dict)
        or set(value) - {"meeting", "binding_task_id", "response_limits", "persona_image"} != FIELDS
        or value["schema"] != SCHEMA
    ):
        raise ValueError("meet_turn_contract_invalid")
    for field in ("task_id", "lease_id", "tenant_id", "project_id"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[field]):
            raise ValueError("meet_turn_scope_invalid")
    deadline = value["deadline"]
    if type(deadline) is not int or not now < deadline <= now + MAX_SECONDS:
        raise ValueError("meet_turn_expired")
    text = value["text"]
    if "binding_task_id" in value and (
        not isinstance(value["binding_task_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value["binding_task_id"])
    ):
        raise ValueError("meet_binding_task_invalid")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 or "\x00" in text:
        raise ValueError("meet_turn_text_invalid")
    if "response_limits" in value:
        validate_response_limits(value["response_limits"])
    if "persona_image" in value:
        from ananta_contracts.meet_persona_image import decode_assignment

        decode_assignment(value["persona_image"], tenant_id=value["tenant_id"], project_id=value["project_id"])
    if "meeting" in value:
        from urllib.parse import urlsplit

        meeting = value["meeting"]
        if not isinstance(meeting, dict) or set(meeting) != {"origin", "room_id", "grant"}:
            raise ValueError("meet_publication_contract_invalid")
        if any(not isinstance(item, str) for item in meeting.values()):
            raise ValueError("meet_publication_contract_invalid")
        origin = urlsplit(meeting["origin"])
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.netloc != origin.hostname
            or meeting["origin"] != f"https://{origin.hostname}"
            or not re.fullmatch(r"room-[a-f0-9]{18}", meeting["room_id"])
            or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", meeting["grant"])
            or len(meeting["grant"]) > 4096
        ):
            raise ValueError("meet_publication_contract_invalid")
    return value


def validate_response_limits(value):
    if not isinstance(value, dict) or set(value) != {"max_output_tokens", "max_reply_chars"}:
        raise ValueError("meet_response_limits_invalid")
    for field, maximum in (("max_output_tokens", 128), ("max_reply_chars", 450)):
        if type(value[field]) is not int or not 1 <= value[field] <= maximum:
            raise ValueError("meet_response_limits_invalid")
    return value


def authenticate(key, body, supplied):
    if len(body) > MAX_REQUEST_BYTES or not hmac.compare_digest(signature(key, body), supplied):
        raise ValueError("meet_turn_unauthorized")


def load_key(path):
    from pathlib import Path

    source = Path(path)
    if not source.is_file() or source.stat().st_mode & 0o077:
        raise ValueError("meet_turn_key_permissions")
    key = source.read_bytes().strip()
    if len(key) < 32 or len(key) > 256:
        raise ValueError("meet_turn_key_invalid")
    return key
