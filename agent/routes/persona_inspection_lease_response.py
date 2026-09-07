"""Read-only inspection authorization; signatures bind exact request and reply."""

import hmac
import re
import time

from flask import jsonify, request

from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.persona_inspection_wire import parse_inspection_json
from worker.meet_media.persona_http import request_signature, result_signature


def inspection_lease_response(*, service, key, domain, validate_assignment):
    if key is None or request.content_length is None or not 0 < request.content_length <= 8192:
        raise PermissionError("persona_lease_invalid")
    raw = request.get_data(cache=False)
    if not hmac.compare_digest(
        request_signature(key, domain, raw), request.headers.get("X-Ananta-Persona-Signature", "")
    ):
        raise PermissionError("persona_lease_unauthorized")
    payload = parse_inspection_json(raw, maximum=8192)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"assignment", "nonce"}
        or not isinstance(payload["nonce"], str)
        or not re.fullmatch(r"[a-f0-9-]{36}", payload["nonce"])
    ):
        raise ValueError("persona_lease_invalid")
    try:
        service.require(validate_assignment(payload["assignment"], time.time()))
        allowed = True
    except (ValueError, PermissionError, ProjectAccessError):
        allowed = False
    response = jsonify({"allowed": allowed})
    response.headers["X-Ananta-Persona-Result-Signature"] = result_signature(key, domain, raw, response.get_data())
    return response
