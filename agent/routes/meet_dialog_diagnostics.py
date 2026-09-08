"""Thin additive endpoints on the existing authenticated Meet blueprint."""

import hmac
import re
import time

from flask import current_app, jsonify, request

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import parse
from ananta_contracts.meet_dialog_diagnostics import (
    MAX_REQUEST_BYTES,
    request_signature,
    response_signature,
    validate_request,
)


def _service():
    if current_app.config.get("ROLE") != "hub":
        raise MeetError("meet_hub_required", 403)
    service = current_app.extensions.get("meet_dialog_diagnostics")
    if service is None:
        raise MeetError("meet_dialog_diagnostics_disabled", 404)
    return service


def callback():
    service = _service()
    key = current_app.extensions.get("meet_media_worker_key")
    if (
        key is None
        or request.headers.get("Authorization") is not None
        or request.args
        or request.headers.get("Transfer-Encoding")
        or request.mimetype != "application/json"
        or request.content_length is None
        or not 0 < request.content_length <= MAX_REQUEST_BYTES
    ):
        raise MeetError("meet_dialog_diagnostics_request_invalid", 403)
    raw = request.get_data(cache=False)
    supplied = request.headers.get("X-Ananta-Dialog-Diagnostics-Signature", "")
    if len(raw) != request.content_length or not re.fullmatch(r"[a-f0-9]{64}", supplied) or not hmac.compare_digest(
        request_signature(key, raw), supplied
    ):
        raise MeetError("meet_dialog_diagnostics_unauthorized", 401)
    try:
        payload = validate_request(parse(raw), time.time())
    except ValueError:
        raise MeetError("meet_dialog_diagnostics_request_invalid") from None
    response = jsonify(service.accept(payload))
    response.headers["X-Ananta-Dialog-Diagnostics-Signature"] = response_signature(key, raw, response.get_data())
    return response


@check_user_auth
def inspect(project, task_id):
    service = _service()
    if (
        request.args
        or request.content_length not in {None, 0}
        or request.headers.get("Transfer-Encoding")
        or request.environ.get("wsgi.input_terminated")
    ):
        raise MeetError("meet_dialog_diagnostics_request_invalid")
    return jsonify(service.inspect(get_authenticated_source_control_principal(), project, task_id))


def register_routes(blueprint):
    blueprint.add_url_rule(
        "/internal/dialog/diagnostics", endpoint="dialog_diagnostics_callback", view_func=callback, methods=["POST"]
    )
    blueprint.add_url_rule(
        "/projects/<project>/dialogs/<task_id>/diagnostics",
        endpoint="dialog_diagnostics_inspect",
        view_func=inspect,
        methods=["GET"],
    )
