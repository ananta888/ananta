"""Shared bounded HTTP inputs and Hub-only service lookup for persona routes."""

from flask import current_app, request

from ananta_contracts.persona_inspection_wire import parse_inspection_json


def revision(value, *, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= 2**53 - 1:
        raise ValueError("persona_revision_invalid")
    return value


def service(name):
    if current_app.config.get("ROLE") != "hub":
        raise PermissionError("persona_hub_required")
    instance = current_app.extensions.get(name)
    if instance is None:
        raise ValueError("persona_disabled")
    return instance


def payload(fields, *, maximum=16384):
    if request.content_length is None or not 0 < request.content_length <= maximum:
        raise ValueError("persona_payload_invalid")
    if not request.is_json:
        raise ValueError("persona_payload_invalid")
    value = parse_inspection_json(request.get_data(cache=False), maximum=maximum)
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("persona_payload_invalid")
    return value
