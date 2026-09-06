"""Shared bounded HTTP inputs and Hub-only service lookup for persona routes."""

from flask import current_app, request


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
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("persona_payload_invalid")
    return value
