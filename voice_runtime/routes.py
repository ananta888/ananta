from __future__ import annotations

import json
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request

from .schemas import ApiError

voice_runtime_bp = Blueprint("voice_runtime", __name__)


@voice_runtime_bp.get("/health")
def health() -> tuple[dict, int]:
    config = current_app.config["voice_runtime_config"]
    return (
        {
            "ok": True,
            "status": "ok",
            "service": "voice-runtime",
            "provider": config.provider,
            "backend": config.backend,
            "loaded_model": config.model,
            "fallback_model": config.fallback_model,
            "device": config.device,
            "backend_fallback_order": list(config.backend_fallback_order),
        },
        HTTPStatus.OK,
    )


@voice_runtime_bp.get("/v1/models")
def models() -> tuple[dict, int]:
    backend = current_app.config["voice_runtime_backend"]
    config = current_app.config["voice_runtime_config"]
    models_payload = backend.list_models()
    return (
        {
            "provider": config.provider,
            "backend": backend.name(),
            "models": models_payload,
        },
        HTTPStatus.OK,
    )


@voice_runtime_bp.post("/v1/audio/transcriptions")
def transcriptions() -> tuple[dict, int]:
    backend = current_app.config["voice_runtime_backend"]
    config = current_app.config["voice_runtime_config"]
    upload, error = _read_audio_upload(config.max_audio_mb)
    if error:
        return error.to_response()

    try:
        result = backend.transcribe(
            filename=upload.filename,
            content=upload.content,
            language=request.form.get("language"),
        )
    except TimeoutError as exc:
        return ApiError(
            code="voice.timeout",
            message=str(exc),
            retriable=True,
            status=HTTPStatus.GATEWAY_TIMEOUT,
        ).to_response()
    except Exception as exc:
        return ApiError(
            code="voice.backend_error",
            message=str(exc),
            retriable=False,
            status=HTTPStatus.BAD_GATEWAY,
        ).to_response()

    return (
        {
            "provider": config.provider,
            "model": result.model or config.model,
            "text": result.text,
            "language": result.language,
            "duration_ms": result.duration_ms,
            "warnings": list(result.warnings),
        },
        HTTPStatus.OK,
    )


@voice_runtime_bp.post("/v1/audio/chat")
def audio_chat() -> tuple[dict, int]:
    backend = current_app.config["voice_runtime_backend"]
    config = current_app.config["voice_runtime_config"]
    upload, error = _read_audio_upload(config.max_audio_mb)
    if error:
        return error.to_response()

    try:
        raw_context = request.form.get("context_json")
        parsed_context = None
        if raw_context:
            try:
                parsed_context = json.loads(raw_context)
            except ValueError:
                parsed_context = None
        result = backend.audio_chat(
            filename=upload.filename,
            content=upload.content,
            context=parsed_context or request.get_json(silent=True),
        )
    except TimeoutError as exc:
        return ApiError(
            code="voice.timeout",
            message=str(exc),
            retriable=True,
            status=HTTPStatus.GATEWAY_TIMEOUT,
        ).to_response()
    except Exception as exc:
        return ApiError(
            code="voice.backend_error",
            message=str(exc),
            retriable=False,
            status=HTTPStatus.BAD_GATEWAY,
        ).to_response()

    return (
        {
            "provider": config.provider,
            "model": config.model,
            "text": result.text,
            "transcript": result.transcript,
            "tool_intent": result.tool_intent,
        },
        HTTPStatus.OK,
    )


class _AudioUpload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content


def _read_audio_upload(max_audio_mb: int) -> tuple[_AudioUpload | None, ApiError | None]:
    audio_file = request.files.get("file")
    if audio_file is None:
        return None, ApiError(code="validation.missing_file", message="multipart file field 'file' is required")
    payload = audio_file.read()
    if not payload:
        return None, ApiError(code="validation.empty_file", message="audio payload must not be empty")

    limit_bytes = max_audio_mb * 1024 * 1024
    if len(payload) > limit_bytes:
        return (
            None,
            ApiError(
                code="validation.file_too_large",
                message=f"audio payload exceeds {max_audio_mb}MB limit",
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            ),
        )
    return _AudioUpload(filename=audio_file.filename or "audio", content=payload), None
