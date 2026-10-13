"""Closed Task-tool backchannel; identities are returned only by the Hub."""

import hashlib
import hmac
import re
from copy import deepcopy

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.meet_dialog import parse_visual_control

MAX_REQUEST_BYTES = 20_000
MAX_RESPONSE_BYTES = 32_000
SIGNATURE_HEADER = "X-Ananta-Companion-Tool-Signature"
REQUEST_SCHEMA = "ananta.meet-companion-task-tool-request.v1"
RESPONSE_SCHEMA = "ananta.meet-companion-task-tool.v1"


def parse(raw, maximum=MAX_RESPONSE_BYTES):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError("meet_companion_task_tool_payload_invalid")
    return parse_visual_control(raw)


def validate_request(value, now):
    if (not isinstance(value, dict)
            or set(value) != {"schema", "nonce", "sent_at", "assignment", "tool", "arguments"}
            or value["schema"] != REQUEST_SCHEMA
            or not isinstance(value["nonce"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
            or type(value["sent_at"]) is not int or not now - 10 <= value["sent_at"] <= now + 3
            or not isinstance(value["assignment"], dict)
            or not isinstance(value["tool"], str) or not re.fullmatch(r"codecompass\.[a-z_]{1,48}", value["tool"])
            or not isinstance(value["arguments"], dict)):
        raise ValueError("meet_companion_task_tool_request_invalid")
    return deepcopy(value)


def validate_response(value, assignment, tool):
    if (not isinstance(value, dict) or set(value) != {"schema", "tool", "text", "truncated", "sources", "run"}
            or value["schema"] != RESPONSE_SCHEMA or value["tool"] != tool
            or not isinstance(value["text"], str) or len(value["text"]) > 6000
            or type(value["truncated"]) is not bool
            or not isinstance(value["sources"], list) or len(value["sources"]) != 1):
        raise ValueError("meet_companion_task_tool_response_invalid")
    source = value["sources"][0]
    if (not isinstance(source, dict) or set(source) != {"source_id", "binding_digest"}
            or not isinstance(source["source_id"], str) or not re.fullmatch(r"SRC_[a-f0-9]{32}", source["source_id"])
            or not isinstance(source["binding_digest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["binding_digest"])):
        raise ValueError("meet_companion_task_tool_source_invalid")
    run = validate_hub_evidence_assignment(value["run"])
    if (run["task_id"] != assignment["task_id"] or run["assignment_id"] != assignment["runtime_id"]
            or run["dispatch_lease_id"] != assignment["lease_id"] or run["evidence_scope"] not in {"local", "test"}):
        raise ValueError("meet_companion_task_tool_run_invalid")
    return deepcopy(value)


def request_signature(key, body):
    return hmac.new(key, b"ananta.meet-companion-task-tool.request.v1\0" + body, hashlib.sha256).hexdigest()


def response_signature(key, request_body, response_body):
    return hmac.new(key, b"ananta.meet-companion-task-tool.response.v1\0" + request_body
                    + b"\0" + response_body, hashlib.sha256).hexdigest()
