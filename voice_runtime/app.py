from __future__ import annotations

import logging
import time
import uuid

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from .backends.router import build_voice_backend_resolver
from .config import VoiceRuntimeConfig
from .metrics import VoiceRuntimeMetrics, operation_for_endpoint
from .model_manifest import load_catalog_for_config
from .parallel import ParallelCandidateExecutor
from .pipeline import TranscriptionPipeline
from .resources import ResourceAdmissionController, resource_budget_from_config
from .routes import voice_runtime_bp
from .streaming import (
    StreamSessionManager,
    container_safe_recognizer_factory,
    policy_streaming_recognizer_factory,
)

_log = logging.getLogger(__name__)


def create_app(config: VoiceRuntimeConfig | None = None) -> Flask:
    app = Flask(__name__)
    runtime_config = config or VoiceRuntimeConfig.from_env()
    runtime_config.validate()
    model_catalog = load_catalog_for_config(runtime_config)
    runtime_metrics = VoiceRuntimeMetrics()
    runtime_metrics.set_privacy_state(
        store_audio_requested=runtime_config.store_audio,
        store_audio_effective=False,
    )
    backend_resolver = build_voice_backend_resolver(
        runtime_config,
        model_catalog=model_catalog,
        metrics=runtime_metrics,
    )
    backend = backend_resolver.route(runtime_config.backend_fallback_order)
    backend_catalog = backend_resolver.catalog(runtime_config.policy_allowed_backends)
    pipeline = TranscriptionPipeline(
        config=runtime_config,
        backend=backend,
        candidate_executor=ParallelCandidateExecutor(
            max_inflight_candidates=runtime_config.max_queue_depth,
            metrics=runtime_metrics,
            admission_controller=ResourceAdmissionController(
                resource_budget_from_config(runtime_config)
            ),
        ),
        backend_resolver=backend_resolver,
    )

    app.config["voice_runtime_config"] = runtime_config
    app.config["voice_runtime_backend"] = backend
    app.config["voice_runtime_backend_catalog"] = backend_catalog
    app.config["voice_runtime_backend_resolver"] = backend_resolver
    app.config["voice_runtime_pipeline"] = pipeline
    app.config["voice_runtime_metrics"] = runtime_metrics
    if runtime_config.enable_streaming:
        incremental_factory = getattr(backend, "streaming_recognizer_factory", None)
        recognizer_factory = container_safe_recognizer_factory(
            backend,
            incremental_factory if callable(incremental_factory) else None,
        )
        app.config["voice_runtime_stream_manager"] = StreamSessionManager(
            recognizer_factory,
            policy_recognizer_factory=policy_streaming_recognizer_factory(
                pipeline,
                backend_resolver,
                runtime_config,
            ),
            max_sessions=runtime_config.max_queue_depth,
            max_total_bytes=runtime_config.max_audio_mb * 1024 * 1024,
            default_deadline_seconds=runtime_config.stream_timeout_sec,
            default_max_audio_seconds=runtime_config.max_audio_duration_sec,
            max_decoded_pcm_bytes=runtime_config.max_decoded_pcm_mb * 1024 * 1024,
            audio_decode_timeout_seconds=min(runtime_config.timeout_sec, 60),
        )
    app.register_blueprint(voice_runtime_bp)

    @app.before_request
    def _start_observation() -> None:
        if request.mimetype == "multipart/form-data":
            request.max_content_length = runtime_config.max_audio_mb * 1024 * 1024 + 256 * 1024
            request.max_form_memory_size = 256 * 1024
            request.max_form_parts = 32
        g.voice_runtime_request_started = time.monotonic()
        g.voice_runtime_request_id = _correlation_id(request.headers.get("X-Request-ID"))

    @app.after_request
    def _finish_observation(response):
        started = float(getattr(g, "voice_runtime_request_started", time.monotonic()))
        duration = max(0.0, time.monotonic() - started)
        operation = operation_for_endpoint(request.endpoint)
        runtime_metrics.observe_http_request(
            endpoint=request.endpoint,
            status_code=response.status_code,
            duration_seconds=duration,
        )
        request_id = str(getattr(g, "voice_runtime_request_id", "") or _correlation_id(None))
        response.headers.setdefault("X-Request-ID", request_id)
        if operation != "metrics":
            _log.info(
                "voice_runtime_request request_id=%s operation=%s status=%s duration_ms=%.3f "
                "store_audio_requested=%s store_audio_effective=false",
                request_id,
                operation,
                response.status_code,
                duration * 1000.0,
                str(runtime_config.store_audio).lower(),
            )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def _request_too_large(_exc: RequestEntityTooLarge):
        payload = {
            "error": {
                "code": "validation.file_too_large",
                "message": f"audio payload exceeds {runtime_config.max_audio_mb}MB limit",
                "retriable": False,
            }
        }
        return jsonify(payload), 413

    @app.errorhandler(Exception)
    def _unhandled(_exc: Exception):
        payload = {"error": {"code": "voice.internal_error", "message": "internal voice error", "retriable": False}}
        return jsonify(payload), 500

    return app


def _correlation_id(value: object) -> str:
    supplied = str(value or "").strip()
    if supplied and len(supplied) <= 128 and all(char.isalnum() or char in "-_.:" for char in supplied):
        return supplied
    return f"voice-{uuid.uuid4().hex}"


if __name__ == "__main__":
    cfg = VoiceRuntimeConfig.from_env()
    create_app(cfg).run(host=cfg.host, port=cfg.port)
