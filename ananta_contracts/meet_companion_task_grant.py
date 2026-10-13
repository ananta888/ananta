"""Task-bound Companion backchannel authentication, separated from legacy grants."""

import hashlib
import hmac
import re
from copy import deepcopy

from ananta_contracts.meet_dialog import OPTIONAL_CONTROL_CAPABILITIES, validate_controls

MAX_REQUEST_BYTES = 1536
MAX_RESPONSE_BYTES = 8192
SIGNATURE_HEADER = "X-Ananta-Companion-Task-Signature"
REQUEST_SCHEMA = "ananta.meet-companion-task-grant-request.v1"
RESPONSE_SCHEMA = "ananta.meet-companion-task-grant.v1"
ASSIGNMENT_FIELDS = ("project_id", "task_id", "lease_id", "runtime_id", "session_id", "capabilities")


def validate_model_execution(model):
    if (not isinstance(model, dict) or set(model) != {"backend", "model", "environment_digest"}
            or not isinstance(model["backend"], str) or model["backend"] not in {"openai", "ollama"}
            or not isinstance(model["model"], str) or not 1 <= len(model["model"]) <= 160
            or not isinstance(model["environment_digest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", model["environment_digest"])):
        raise ValueError("meet_companion_model_profile_invalid")
    return deepcopy(model)


def validate_response(value, assignment, now):
    fields = {"schema", "origin", "room_id", "grant", "deadline", "controls", *ASSIGNMENT_FIELDS}
    if isinstance(value, dict) and "model_execution" in value:
        validate_model_execution(value["model_execution"])
        fields.add("model_execution")
    if (not isinstance(value, dict) or set(value) != fields or value["schema"] != RESPONSE_SCHEMA
            or any(value[key] != assignment[key] for key in ASSIGNMENT_FIELDS)
            or value["origin"] != assignment["meeting"]["origin"]
            or value["room_id"] != assignment["meeting"]["room_id"]
            or type(value["deadline"]) is not int or not now < value["deadline"] <= assignment["deadline"]
            or not isinstance(value["grant"], str) or not 0 < len(value["grant"]) <= 4096):
        raise ValueError("meet_companion_task_grant_response_invalid")
    controls = validate_controls(value["controls"])
    if (controls["audio"]["enabled"] or controls["screen"]["enabled"]
            or any((name in controls) != (capability in assignment["capabilities"])
                   for name, capability in OPTIONAL_CONTROL_CAPABILITIES.items())):
        raise ValueError("meet_companion_task_grant_controls_invalid")
    return deepcopy(value)


def validate_request(value, now):
    if (not isinstance(value, dict) or set(value) != {"schema", "nonce", "sent_at", "assignment"}
            or value["schema"] != REQUEST_SCHEMA or not isinstance(value["assignment"], dict)
            or not isinstance(value["nonce"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
            or type(value["sent_at"]) is not int or not now - 10 <= value["sent_at"] <= now + 3):
        raise ValueError("meet_companion_task_grant_request_invalid")
    return value


def request_signature(key, body):
    return hmac.new(key, b"ananta.meet-companion-task-grant.request.v1\0" + body, hashlib.sha256).hexdigest()


def response_signature(key, request_body, response_body):
    return hmac.new(key, b"ananta.meet-companion-task-grant.response.v1\0" + request_body
                    + b"\0" + response_body, hashlib.sha256).hexdigest()
