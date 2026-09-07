"""Closed HTTP adapter; allocation policy and binding CAS live outside routes."""

import json

from flask import current_app, jsonify, request

from agent.auth import get_authenticated_source_control_principal
from agent.services.meet_contract import MeetError


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def allocate_binding(project, task):
    service = current_app.extensions.get("meet_room_allocation")
    if service is None:
        raise MeetError("meet_room_allocation_disabled", 404)
    if (
        request.args
        or request.headers.get("Transfer-Encoding")
        or not request.is_json
        or request.content_length is None
        or not 0 < request.content_length <= 256
    ):
        raise MeetError("meet_room_allocation_payload_invalid")
    try:
        value = json.loads(request.get_data(cache=False).decode("utf-8"), object_pairs_hook=_unique_fields)
    except (ValueError, UnicodeError, RecursionError):
        raise MeetError("meet_room_allocation_payload_invalid") from None
    return jsonify(service.allocate(get_authenticated_source_control_principal(), project, task, value))
