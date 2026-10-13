"""Bounded authenticated access to existing Task-owned Companion grant authority."""

import hmac
import re
import time

from flask import current_app, jsonify, request

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_companion_task_grant import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    SIGNATURE_HEADER,
    request_signature,
    response_signature,
    validate_request,
)
from ananta_contracts.meet_dialog import parse


def register_routes(blueprint):
    @blueprint.post("/internal/companion/task-grant")
    def companion_task_grant():
        if current_app.config.get("ROLE") != "hub":
            raise MeetError("meet_hub_required", 403)
        service = current_app.extensions.get("meet_companion_task_grants")
        key = current_app.extensions.get("meet_media_worker_key")
        if service is None or key is None:
            raise MeetError("meet_companion_task_grants_disabled", 404)
        if (request.args or request.headers.get("Authorization") or request.headers.get("Transfer-Encoding")
                or request.mimetype != "application/json" or request.content_length is None
                or not 0 < request.content_length <= MAX_REQUEST_BYTES):
            raise MeetError("meet_companion_assignment_invalid", 403)
        raw = request.get_data(cache=False)
        signature = request.headers.get(SIGNATURE_HEADER, "")
        if (not re.fullmatch(r"[a-f0-9]{64}", signature)
                or not hmac.compare_digest(request_signature(key, raw), signature)):
            raise MeetError("meet_companion_assignment_unauthorized", 401)
        try:
            payload = validate_request(parse(raw), time.time())
        except ValueError:
            raise MeetError("meet_companion_assignment_invalid", 403) from None
        response = jsonify(service.issue(payload["assignment"]))
        if len(response.get_data()) > MAX_RESPONSE_BYTES:
            raise MeetError("meet_companion_task_grant_oversize", 502)
        response.headers[SIGNATURE_HEADER] = response_signature(key, raw, response.get_data())
        return response
