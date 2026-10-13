"""Closed signed model-run lifecycle; the Hub issues every evidence identity."""

import hashlib
import hmac
import json
import re
from copy import deepcopy

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.meet_companion_task_tool import parse as parse

MAX_REQUEST_BYTES = 128_000
MAX_RESPONSE_BYTES = 32_000
HELPER_INPUT_LIMIT = 160_000  # Full validated assignment/key envelope plus bounded HTTP payload.
SIGNATURE_HEADER = "X-Ananta-Companion-Model-Signature"
REQUEST_SCHEMA = "ananta.meet-companion-model-request.v1"


def _pin(value):
    if (not isinstance(value, dict) or set(value) != {"source_id", "binding_digest"}
            or not isinstance(value["source_id"], str) or not re.fullmatch(r"SRC_[a-f0-9]{32}", value["source_id"])
            or not isinstance(value["binding_digest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["binding_digest"])):
        raise ValueError("meet_companion_model_source_invalid")


def _run(value, assignment):
    run = validate_hub_evidence_assignment(value)
    if (run["task_id"] != assignment["task_id"] or run["assignment_id"] != assignment["runtime_id"]
            or run["dispatch_lease_id"] != assignment["lease_id"] or run["evidence_scope"] not in {"local", "test"}):
        raise ValueError("meet_companion_model_run_invalid")


def result_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def validate_request(value, now):
    if (not isinstance(value, dict)
            or set(value) != {"schema", "nonce", "sent_at", "assignment", "action", "payload"}
            or value["schema"] != REQUEST_SCHEMA or not isinstance(value["nonce"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) or type(value["sent_at"]) is not int
            or not now - 10 <= value["sent_at"] <= now + 3 or not isinstance(value["assignment"], dict)
            or not isinstance(value["action"], str) or value["action"] not in {"reserve", "finish"}
            or not isinstance(value["payload"], dict)):
        raise ValueError("meet_companion_model_request_invalid")
    payload = value["payload"]
    if value["action"] == "reserve":
        if (set(payload) != {"backend", "model", "request", "tool_results"}
                or not isinstance(payload["backend"], str) or payload["backend"] not in {"ollama", "openai"}
                or not isinstance(payload["model"], str) or not 1 <= len(payload["model"]) <= 160
                or not isinstance(payload["request"], dict)
                or not isinstance(payload["tool_results"], list) or len(payload["tool_results"]) > 16):
            raise ValueError("meet_companion_model_request_invalid")
    else:
        if set(payload) != {"run", "input_source", "result"} or not isinstance(payload["result"], dict):
            raise ValueError("meet_companion_model_request_invalid")
        _run(payload["run"], value["assignment"])
        _pin(payload["input_source"])
    return deepcopy(value)


def validate_response(value, assignment, action, payload):
    if not isinstance(value, dict):
        raise ValueError("meet_companion_model_response_invalid")
    fields = ({"schema", "run", "input_source"} if action == "reserve"
              else {"schema", "run", "result_digest", "sources"})
    schema = ("ananta.meet-companion-model-reserved.v1" if action == "reserve"
              else "ananta.meet-companion-model-completed.v1")
    if set(value) != fields or value["schema"] != schema:
        raise ValueError("meet_companion_model_response_invalid")
    _run(value["run"], assignment)
    if action == "reserve":
        _pin(value["input_source"])
        if value["input_source"]["source_id"] not in value["run"]["source_ids"]:
            raise ValueError("meet_companion_model_input_source_invalid")
    else:
        if (value["run"] != payload["run"] or value["result_digest"] != result_digest(payload["result"])
                or not isinstance(value["sources"], list) or len(value["sources"]) != 1):
            raise ValueError("meet_companion_model_result_binding_invalid")
        _pin(value["sources"][0])
    return deepcopy(value)


def request_signature(key, body):
    return hmac.new(key, b"ananta.meet-companion-model.request.v1\0" + body, hashlib.sha256).hexdigest()


def response_signature(key, request_body, response_body):
    return hmac.new(key, b"ananta.meet-companion-model.response.v1\0" + request_body + b"\0" + response_body,
                    hashlib.sha256).hexdigest()
