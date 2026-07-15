from __future__ import annotations

import json
import logging
import os
import resource
import secrets
import uuid
from http import HTTPStatus

from flask import Blueprint, Response, current_app, g, request

from ananta_contracts.model_capability import ModelCapability, ModelStatus

from .context import VoiceRecognitionContext
from .errors import VoiceRuntimeError
from .execution_policy import VoiceExecutionPolicy
from .metrics import VoiceRuntimeMetrics
from .schemas import ApiError
from .streaming import STREAM_SCHEMA_VERSION, StreamProtocolError

_log = logging.getLogger(__name__)

voice_runtime_bp = Blueprint("voice_runtime", __name__)


@voice_runtime_bp.get("/health")
def health() -> tuple[dict, int]:
    config = current_app.config["voice_runtime_config"]
    backend = current_app.config["voice_runtime_backend"]
    pipeline = current_app.config["voice_runtime_pipeline"]
    models_payload = backend.list_models()
    ready_backends = [
        item for item in models_payload if str(item.get("status") or "").lower() in {"ready", "available"}
    ]
    status = "ready" if ready_backends else "degraded"
    status_code = HTTPStatus.OK if ready_backends or not config.production_profile else HTTPStatus.SERVICE_UNAVAILABLE
    return (
        {
            "ok": bool(ready_backends) or not config.production_profile,
            "status": status,
            "service": "voice-runtime",
            "provider": config.provider,
            "backend": config.backend,
            "loaded_model": config.model,
            "fallback_model": config.fallback_model,
            "device": config.device,
            "backend_fallback_order": list(config.backend_fallback_order),
            "transcription_pipeline": config.transcription_pipeline,
            "vad_backend": config.vad_backend,
            "asr_backend": config.asr_backend,
            "postprocess_backend": config.postprocess_backend,
            "confidence_rerun_enabled": config.confidence_rerun_enabled,
            "diarization_backend": config.diarization_backend,
            "enable_streaming": config.enable_streaming,
            "store_audio": config.store_audio,
            "resources": _runtime_resources(),
            "ready_backend_count": len(ready_backends),
            "runtime_capabilities": pipeline.runtime_capabilities(),
        },
        status_code,
    )


@voice_runtime_bp.get("/metrics")
def metrics() -> Response | tuple[dict, int]:
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    runtime_metrics = _runtime_metrics()
    return Response(runtime_metrics.render(), status=HTTPStatus.OK, content_type=runtime_metrics.content_type)


@voice_runtime_bp.get("/v1/models")
def models() -> tuple[dict, int]:
    unauthorized = _require_internal_auth()
    if unauthorized:
        return unauthorized.to_response()
    backend = current_app.config["voice_runtime_backend"]
    backend_catalog = current_app.config["voice_runtime_backend_catalog"]
    config = current_app.config["voice_runtime_config"]
    pipeline = current_app.config["voice_runtime_pipeline"]
    models_payload = backend_catalog.list_models()
    return (
        {
            "provider": config.provider,
            "backend": backend.name(),
            "models": models_payload,
            "capability_catalog": [_capability_entry(item, config=config) for item in models_payload],
            "supported_pipelines": [
                "simple",
                "oldschool_light",
                "whisper_cpp",
                "realtime_streaming",
                "meeting",
                "confidence_rerun",
                "custom",
            ],
            "supported_asr_backends": ["mock", "voxtral", "vosk", "whisper_cpp", "faster_whisper"],
            "supported_diarization_backends": ["none", "mock", "pyannote"],
            "supported_enhancement_variants": ["original", "bypass", "normalized", "high_pass", "speech_safe"],
            "runtime_capabilities": pipeline.runtime_capabilities(),
        },
        HTTPStatus.OK,
    )


@voice_runtime_bp.post("/v1/audio/transcriptions")
def transcriptions() -> tuple[dict, int]:
    pipeline = current_app.config["voice_runtime_pipeline"]
    config = current_app.config["voice_runtime_config"]
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    upload, error = _read_audio_upload(config.max_audio_mb, request_id=request_id)
    if error:
        return error.to_response()
    assert upload is not None

    try:
        context = _parse_recognition_context(request.form.get("recognition_context_json"))
        result = pipeline.transcribe(
            filename=upload.filename,
            content=upload.content,
            language=request.form.get("language"),
            context=context,
        )
    except ValueError:
        return ApiError(
            code="voice.invalid_context",
            message="voice recognition context is invalid",
            retriable=False,
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            request_id=request_id,
        ).to_response()
    except TimeoutError:
        return ApiError(
            code="voice.timeout",
            message="voice backend timed out",
            retriable=True,
            status=HTTPStatus.GATEWAY_TIMEOUT,
            request_id=request_id,
        ).to_response()
    except VoiceRuntimeError as exc:
        status = {
            "invalid_input": HTTPStatus.BAD_REQUEST,
            "policy_blocked": HTTPStatus.FORBIDDEN,
            "unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
            "resource_exhausted": HTTPStatus.TOO_MANY_REQUESTS,
        }.get(exc.code, HTTPStatus.BAD_GATEWAY)
        return ApiError(
            code=f"voice.{exc.code}",
            message=exc.message,
            retriable=exc.retriable,
            status=status,
            request_id=request_id,
        ).to_response()
    except Exception as exc:
        _log.warning("voice transcription failed request_id=%s type=%s", request_id, type(exc).__name__)
        return ApiError(
            code="voice.backend_error",
            message="voice backend failed",
            retriable=False,
            status=HTTPStatus.BAD_GATEWAY,
            request_id=request_id,
        ).to_response()

    payload = result.as_dict()
    _runtime_metrics().observe_transcription_result(result)
    payload.update({"provider": config.provider, "model": result.model or config.model, "request_id": request_id})
    return (
        payload,
        HTTPStatus.OK,
    )


@voice_runtime_bp.post("/v1/audio/chat")
def audio_chat() -> tuple[dict, int]:
    backend = current_app.config["voice_runtime_backend"]
    config = current_app.config["voice_runtime_config"]
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    upload, error = _read_audio_upload(config.max_audio_mb, request_id=request_id)
    if error:
        return error.to_response()
    assert upload is not None

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
    except TimeoutError:
        return ApiError(
            code="voice.timeout",
            message="voice backend timed out",
            retriable=True,
            status=HTTPStatus.GATEWAY_TIMEOUT,
            request_id=request_id,
        ).to_response()
    except Exception as exc:
        _log.warning("voice audio chat failed request_id=%s type=%s", request_id, type(exc).__name__)
        return ApiError(
            code="voice.backend_error",
            message="voice backend failed",
            retriable=False,
            status=HTTPStatus.BAD_GATEWAY,
            request_id=request_id,
        ).to_response()

    return (
        {
            "provider": config.provider,
            "model": config.model,
            "text": result.text,
            "transcript": result.transcript,
            "tool_intent": result.tool_intent,
            "request_id": request_id,
        },
        HTTPStatus.OK,
    )


@voice_runtime_bp.post("/v1/audio/streams")
def create_stream() -> tuple[dict, int]:
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    manager = current_app.config.get("voice_runtime_stream_manager")
    if manager is None:
        return ApiError(
            code="voice.streaming_disabled",
            message="voice streaming is disabled",
            status=HTTPStatus.NOT_IMPLEMENTED,
            request_id=request_id,
        ).to_response()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return ApiError(
            code="validation.invalid_json",
            message="JSON object body is required",
            status=HTTPStatus.BAD_REQUEST,
            request_id=request_id,
        ).to_response()
    try:
        raw_context = body.get("recognition_context")
        if raw_context is not None and not isinstance(raw_context, dict):
            raise ValueError("recognition_context must be an object")
        recognition_context = VoiceRecognitionContext.from_mapping(raw_context) if raw_context is not None else None
        runtime_config = current_app.config["voice_runtime_config"]
        execution_policy = (
            VoiceExecutionPolicy.resolve(runtime_config, recognition_context.configuration)
            if recognition_context is not None
            else None
        )
        policy_metadata = (
            {
                "source": execution_policy.source,
                "recognition_strategy": execution_policy.recognition_strategy,
                "routing_strategy": execution_policy.routing_strategy,
                "correction_policy": execution_policy.correction_policy,
                "primary_backend": execution_policy.primary_backend,
                "secondary_backends": list(execution_policy.secondary_backends),
                "max_parallel_backends": execution_policy.max_parallel_backends,
                "candidate_deadline_sec": execution_policy.candidate_deadline_sec,
                "adjustments": [dict(item) for item in execution_policy.adjustments],
            }
            if execution_policy is not None
            else {}
        )
        session = manager.create(
            filename=str(body.get("filename") or "stream.pcm")[:255],
            language=str(body.get("language") or "").strip() or None,
            media_type=str(body.get("media_type") or "audio/pcm;rate=16000;channels=1"),
            deadline_seconds=_optional_float(body.get("deadline_seconds")),
            max_audio_seconds=_optional_float(body.get("max_audio_seconds")),
            requested_session_id=body.get("requested_session_id"),
            recognition_context=recognition_context,
            execution_policy=policy_metadata,
        )
    except ValueError:
        return ApiError(
            code="voice.invalid_context",
            message="voice recognition context is invalid",
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            request_id=request_id,
        ).to_response()
    except StreamProtocolError as exc:
        return _stream_error(exc, request_id=request_id)
    _runtime_metrics().observe_stream_event("created")
    return (
        {
            "schema_version": STREAM_SCHEMA_VERSION,
            "request_id": request_id,
            "session_id": session.session_id,
            "state": session.state.value,
            "next_chunk_sequence": session.next_chunk_sequence,
            "max_audio_seconds": session.max_audio_seconds,
            "execution_policy": dict(session.execution_policy),
        },
        HTTPStatus.CREATED,
    )


@voice_runtime_bp.put("/v1/audio/streams/<session_id>/chunks/<int:chunk_sequence>")
def stream_chunk(session_id: str, chunk_sequence: int) -> tuple[dict, int]:
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    try:
        manager = _stream_manager()
        event = manager.get(session_id).push(chunk_sequence=chunk_sequence, content=request.get_data(cache=False))
    except StreamProtocolError as exc:
        return _stream_error(exc, request_id=request_id)
    _runtime_metrics().observe_stream_event(
        event.event_type,
        accepted_bytes=event.payload.get("accepted_bytes"),
    )
    return ({"request_id": request_id, "event": event.as_dict()}, HTTPStatus.ACCEPTED)


@voice_runtime_bp.post("/v1/audio/streams/<session_id>/finalize")
def finalize_stream(session_id: str) -> tuple[dict, int]:
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    try:
        session = _stream_manager().get(session_id)
        event = session.finalize()
    except StreamProtocolError as exc:
        return _stream_error(exc, request_id=request_id)
    except Exception as exc:
        _log.warning("voice stream finalize failed request_id=%s type=%s", request_id, type(exc).__name__)
        _runtime_metrics().observe_stream_event("error", outcome="server_error")
        return ApiError(
            code="voice.backend_error",
            message="voice backend failed",
            status=HTTPStatus.BAD_GATEWAY,
            request_id=request_id,
        ).to_response()
    _runtime_metrics().observe_stream_event(event.event_type)
    if event.event_type == "final" and session.result is not None:
        _runtime_metrics().observe_transcription_result(session.result)
    return ({"request_id": request_id, "event": event.as_dict()}, HTTPStatus.OK)


@voice_runtime_bp.get("/v1/audio/streams/<session_id>")
def get_stream(session_id: str) -> tuple[dict, int]:
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    try:
        after_event = int(request.args.get("after_event", -1))
        snapshot = _stream_manager().get(session_id).snapshot(after_event=after_event)
    except (TypeError, ValueError):
        return ApiError(
            code="validation.invalid_cursor",
            message="after_event must be an integer",
            status=HTTPStatus.BAD_REQUEST,
            request_id=request_id,
        ).to_response()
    except StreamProtocolError as exc:
        return _stream_error(exc, request_id=request_id)
    return ({**snapshot, "request_id": request_id}, HTTPStatus.OK)


@voice_runtime_bp.delete("/v1/audio/streams/<session_id>")
def delete_stream(session_id: str) -> tuple[dict, int]:
    request_id = _request_id()
    unauthorized = _require_internal_auth(request_id=request_id)
    if unauthorized:
        return unauthorized.to_response()
    try:
        deleted = _stream_manager().delete(session_id)
    except StreamProtocolError as exc:
        return _stream_error(exc, request_id=request_id)
    if deleted:
        _runtime_metrics().observe_stream_event("closed")
    return ({"request_id": request_id, "deleted": deleted}, HTTPStatus.OK)


class _AudioUpload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content


def _read_audio_upload(
    max_audio_mb: int, *, request_id: str | None = None
) -> tuple[_AudioUpload | None, ApiError | None]:
    audio_file = request.files.get("file")
    if audio_file is None:
        return None, ApiError(
            code="validation.missing_file", message="multipart file field 'file' is required", request_id=request_id
        )
    limit_bytes = max_audio_mb * 1024 * 1024
    payload = audio_file.stream.read(limit_bytes + 1)
    if not payload:
        return None, ApiError(
            code="validation.empty_file", message="audio payload must not be empty", request_id=request_id
        )

    if len(payload) > limit_bytes:
        return (
            None,
            ApiError(
                code="validation.file_too_large",
                message=f"audio payload exceeds {max_audio_mb}MB limit",
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                request_id=request_id,
            ),
        )
    return _AudioUpload(filename=audio_file.filename or "audio", content=payload), None


def _request_id() -> str:
    existing = str(getattr(g, "voice_runtime_request_id", "") or "")
    if existing:
        return existing
    supplied = str(request.headers.get("X-Request-ID") or "").strip()
    if supplied and len(supplied) <= 128 and all(char.isalnum() or char in "-_.:" for char in supplied):
        return supplied
    return f"voice-{uuid.uuid4().hex}"


def _require_internal_auth(*, request_id: str | None = None) -> ApiError | None:
    config = current_app.config["voice_runtime_config"]
    expected = str(config.internal_service_token or "")
    if not expected and not config.production_profile:
        return None
    supplied = str(request.headers.get("X-Ananta-Internal-Token") or "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        return ApiError(
            code="voice.internal_auth_required",
            message="valid internal service authentication is required",
            status=HTTPStatus.UNAUTHORIZED,
            request_id=request_id,
        )
    return None


def _stream_manager():
    manager = current_app.config.get("voice_runtime_stream_manager")
    if manager is None:
        raise StreamProtocolError("voice.streaming_disabled", "voice streaming is disabled", 501)
    return manager


def _stream_error(exc: StreamProtocolError, *, request_id: str) -> tuple[dict, int]:
    _runtime_metrics().observe_stream_event("error", outcome=exc.code)
    return ApiError(
        code=exc.code,
        message=exc.message,
        retriable=exc.retriable,
        status=exc.status_code,
        request_id=request_id,
    ).to_response()


def _runtime_metrics() -> VoiceRuntimeMetrics:
    metrics_service = current_app.config.get("voice_runtime_metrics")
    if not isinstance(metrics_service, VoiceRuntimeMetrics):
        raise RuntimeError("voice runtime metrics are unavailable")
    return metrics_service


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if not isinstance(value, (str, int, float)):
            raise TypeError
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StreamProtocolError("validation.invalid_deadline", "deadline_seconds must be numeric", 422) from exc


def _parse_recognition_context(raw: str | None) -> VoiceRecognitionContext | None:
    if not raw:
        return None
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise ValueError("voice recognition context is too large")
    payload = json.loads(raw)
    return VoiceRecognitionContext.from_mapping(payload)


def _capability_entry(item: dict, *, config) -> dict:
    raw_status = str(item.get("status") or "unavailable").lower()
    status = ModelStatus.READY if raw_status in {"ready", "available"} else ModelStatus.UNAVAILABLE
    if raw_status == "degraded":
        status = ModelStatus.DEGRADED
    engine = str(item.get("engine") or "unknown")
    capabilities = [str(value) for value in (item.get("capabilities") or [])]
    capability = ModelCapability(
        id=str(item.get("id") or engine),
        engine=engine,
        revision=str(item.get("revision") or "unverified"),
        tasks=("transcription",),
        languages=tuple(str(value) for value in (item.get("languages") or [])),
        device=str(item.get("device") or item.get("device_preference") or config.device),
        quantization=str(item.get("quantization") or "unknown"),
        license=str(item.get("license") or "unknown"),
        status=status,
        manifest_digest=str(item.get("manifest_digest") or "unverified"),
        extensions={
            "voice": {
                "batch": "transcription" in capabilities,
                "streaming": engine == "vosk" and config.enable_streaming,
                "word_timestamps": "word_timestamps" in capabilities,
                "synthetic": bool(item.get("synthetic", False)),
                "enhancement_variants": list(config.enhancement_variants),
                "adaptive_local": bool(config.adaptive_routing_enabled),
                "diarization": config.diarization_backend,
            }
        },
    )
    return capability.as_dict()


def _runtime_resources() -> dict[str, int | float | None]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # Linux ru_maxrss is KiB. The runtime images are Linux-only; report an
    # explicit unit in the key and avoid host-wide identifiers.
    return {
        "process_peak_rss_mb": round(float(usage.ru_maxrss) / 1024.0, 3),
        "cpu_count": os.cpu_count(),
    }
