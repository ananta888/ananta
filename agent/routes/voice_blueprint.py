"""Voice blueprint with parser-level request-body limits and its 413 error handler."""

from __future__ import annotations

from flask import (
    Blueprint,
    request,
)
from werkzeug.exceptions import RequestEntityTooLarge

from agent.common.errors import api_response
from agent.routes.voice_request_support import _max_audio_mb

voice_bp = Blueprint("voice", "agent.routes.voice")
_VOICE_MULTIPART_OVERHEAD_BYTES = 256 * 1024
_VOICE_MAX_FORM_MEMORY_BYTES = 256 * 1024
_VOICE_MAX_FORM_PARTS = 32


@voice_bp.before_request
def _bound_voice_request_body_before_form_parsing() -> None:
    """Apply parser-level limits before Werkzeug materializes multipart data."""

    if request.mimetype == "multipart/form-data":
        request.max_content_length = _max_audio_mb() * 1024 * 1024 + _VOICE_MULTIPART_OVERHEAD_BYTES
        request.max_form_memory_size = _VOICE_MAX_FORM_MEMORY_BYTES
        request.max_form_parts = _VOICE_MAX_FORM_PARTS
    elif request.endpoint == "voice.push_voice_stream_chunk":
        request.max_content_length = 1024 * 1024


@voice_bp.errorhandler(RequestEntityTooLarge)
def _voice_request_too_large(_exc: RequestEntityTooLarge):
    if request.endpoint == "voice.push_voice_stream_chunk":
        return api_response(
            status="error",
            code=413,
            data={
                "error": {
                    "code": "voice_stream.invalid_chunk",
                    "message": "chunk must contain at most 1MB",
                }
            },
        )
    return api_response(
        status="error",
        code=413,
        data={
            "error": {
                "code": "validation.file_too_large",
                "message": f"voice request exceeds {_max_audio_mb()}MB audio limit",
            }
        },
    )
