"""Dedicated authenticated Task-tool transport; no legacy endpoint fallback."""

import hmac
import re
import time

from flask import current_app, jsonify, request

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_companion_task_tool import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    SIGNATURE_HEADER,
    parse,
    request_signature,
    response_signature,
    validate_request,
)


def register_routes(blueprint):
    @blueprint.post("/internal/companion/task-tool")
    def companion_task_tool():
        if current_app.config.get("ROLE") != "hub":
            raise MeetError("meet_hub_required", 403)
        service = current_app.extensions.get("meet_companion_task_tools")
        key = current_app.extensions.get("meet_media_worker_key")
        if service is None or key is None:
            raise MeetError("meet_companion_task_tools_disabled", 404)
        if (request.args or request.headers.get("Authorization") or request.headers.get("Transfer-Encoding")
                or request.mimetype != "application/json" or request.content_length is None
                or not 0 < request.content_length <= MAX_REQUEST_BYTES):
            raise MeetError("meet_companion_task_tool_request_invalid", 403)
        raw = request.get_data(cache=False)
        supplied = request.headers.get(SIGNATURE_HEADER, "")
        if (not re.fullmatch(r"[a-f0-9]{64}", supplied)
                or not hmac.compare_digest(request_signature(key, raw), supplied)):
            raise MeetError("meet_companion_task_tool_unauthorized", 401)
        try:
            value = validate_request(parse(raw, MAX_REQUEST_BYTES), time.time())
        except ValueError:
            raise MeetError("meet_companion_task_tool_request_invalid", 403) from None
        response = jsonify(service.call(value))
        if len(response.get_data()) > MAX_RESPONSE_BYTES:
            raise MeetError("meet_companion_task_tool_response_oversize", 502)
        response.headers[SIGNATURE_HEADER] = response_signature(key, raw, response.get_data())
        return response
