"""Authenticated bounded HTTP entrypoint for the transcript-corrector worker."""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from collections import Counter
from collections.abc import Iterable
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ananta_contracts.voice_corrector_worker import (
    CONTRACT_VERSION,
    VoiceCorrectorContractError,
    VoiceCorrectorWorkerRequest,
    VoiceCorrectorWorkerResponse,
    build_edits,
    edit_ratio,
)
from worker.runtime.generative_corrector_engine import (
    EmbeddedTransformersGenerativeCorrectorEngine,
    GenerativeCorrectorEngine,
)
from worker.runtime.generative_corrector_provider_engine import (
    CompositeGenerativeCorrectorEngine,
    CorrectorProviderEndpoint,
    ProviderGenerativeCorrectorEngine,
)

DEFAULT_PORT = 8093
CORRECTOR_ENDPOINT = "/internal/v1/voice-corrector"
_DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
_PROTECTED_TOKEN_RE = re.compile(r"https?://\S+|\b[\w.:-]*\d[\w.:-]*\b", re.UNICODE)


def _engine_from_environment() -> GenerativeCorrectorEngine | None:
    mode = str(os.getenv("GENERATIVE_CORRECTOR_ENGINE", "")).strip().lower()
    if mode not in {"transformers", "providers", "hybrid"}:
        return None
    engines: list[GenerativeCorrectorEngine] = []
    if mode in {"transformers", "hybrid"}:
        model_root = str(os.getenv("GENERATIVE_CORRECTOR_MODEL_ROOT", "")).strip()
        catalog_path = str(os.getenv("GENERATIVE_CORRECTOR_MODEL_CATALOG", "")).strip()
        if model_root and catalog_path:
            engines.append(
                EmbeddedTransformersGenerativeCorrectorEngine(
                    model_root=model_root,
                    catalog_path=catalog_path,
                    device=str(os.getenv("GENERATIVE_CORRECTOR_DEVICE", "cpu")).strip().lower(),
                    max_input_chars=_env_int(
                        "GENERATIVE_CORRECTOR_MAX_INPUT_CHARS", 32_000, minimum=1_024, maximum=512_000
                    ),
                    max_input_tokens=_env_int(
                        "GENERATIVE_CORRECTOR_MAX_INPUT_TOKENS", 4_096, minimum=128, maximum=32_768
                    ),
                    max_new_tokens=_env_int("GENERATIVE_CORRECTOR_MAX_NEW_TOKENS", 1_024, minimum=16, maximum=4_096),
                )
            )
    if mode in {"providers", "hybrid"}:
        endpoints = _provider_endpoints_from_environment()
        if endpoints:
            engines.append(
                ProviderGenerativeCorrectorEngine(
                    endpoints,
                    discovery_timeout_seconds=_env_int(
                        "GENERATIVE_CORRECTOR_PROVIDER_DISCOVERY_TIMEOUT_MS",
                        200,
                        minimum=200,
                        maximum=5_000,
                    )
                    / 1000.0,
                    response_max_bytes=_env_int(
                        "GENERATIVE_CORRECTOR_PROVIDER_MAX_RESPONSE_BYTES",
                        1024 * 1024,
                        minimum=4_096,
                        maximum=2 * 1024 * 1024,
                    ),
                    max_output_tokens=_env_int(
                        "GENERATIVE_CORRECTOR_MAX_NEW_TOKENS",
                        1_024,
                        minimum=16,
                        maximum=4_096,
                    ),
                )
            )
    if len(engines) == 1:
        return engines[0]
    return CompositeGenerativeCorrectorEngine(engines) if engines else None


def _provider_endpoints_from_environment() -> tuple[CorrectorProviderEndpoint, ...]:
    allowed = {
        item.strip().lower()
        for item in str(os.getenv("GENERATIVE_CORRECTOR_EXTERNAL_PROVIDERS", "")).split(",")
        if item.strip()
    }
    configured: list[CorrectorProviderEndpoint] = []
    for provider_id, url_name, key_name in (
        ("ollama", "GENERATIVE_CORRECTOR_OLLAMA_URL", "GENERATIVE_CORRECTOR_OLLAMA_API_KEY"),
        ("lmstudio", "GENERATIVE_CORRECTOR_LMSTUDIO_URL", "GENERATIVE_CORRECTOR_LMSTUDIO_API_KEY"),
    ):
        base_url = str(os.getenv(url_name, "")).strip()
        if provider_id not in allowed or not base_url:
            continue
        configured.append(
            CorrectorProviderEndpoint(
                provider_id=provider_id,
                base_url=base_url,
                api_key=str(os.getenv(key_name, "")).strip() or None,
            )
        )
    return tuple(configured)


def create_app(
    *,
    engine: GenerativeCorrectorEngine | None = None,
    auth_token: str | None = None,
    allowed_hub_origins: Iterable[str] | None = None,
    max_request_bytes: int | None = None,
    max_in_flight: int | None = None,
) -> Flask:
    configured_engine = engine if engine is not None else _engine_from_environment()
    expected_token = str(
        auth_token if auth_token is not None else os.getenv("GENERATIVE_CORRECTOR_INTERNAL_TOKEN", "")
    ).strip()
    raw_origins = (
        tuple(allowed_hub_origins)
        if allowed_hub_origins is not None
        else tuple(
            item.strip()
            for item in str(os.getenv("GENERATIVE_CORRECTOR_ALLOWED_HUB_ORIGINS", "")).split(",")
            if item.strip()
        )
    )
    origins = frozenset(_normalize_origin(item) for item in raw_origins)
    request_limit = (
        int(max_request_bytes)
        if max_request_bytes is not None
        else _env_int(
            "GENERATIVE_CORRECTOR_MAX_REQUEST_BYTES",
            _DEFAULT_MAX_REQUEST_BYTES,
            minimum=1_024,
            maximum=4 * 1024 * 1024,
        )
    )
    concurrency = (
        int(max_in_flight)
        if max_in_flight is not None
        else _env_int("GENERATIVE_CORRECTOR_MAX_IN_FLIGHT", 1, minimum=1, maximum=8)
    )
    if not 1_024 <= request_limit <= 4 * 1024 * 1024:
        raise ValueError("generative corrector request limit is outside its bounds")
    if not 1 <= concurrency <= 8:
        raise ValueError("generative corrector concurrency is outside its bounds")
    slots = threading.BoundedSemaphore(concurrency)

    app = Flask("ananta-generative-corrector-worker")
    app.config["MAX_CONTENT_LENGTH"] = request_limit

    @app.get("/health")
    def health() -> tuple[Any, int]:
        auth_configured = len(expected_token) >= 24
        snapshot = _engine_health_snapshot(configured_engine)
        model_ids = list(snapshot["model_ids"])
        provider_ids = list(snapshot["provider_ids"])
        ready_provider_ids = list(snapshot["ready_provider_ids"])
        configured = bool(configured_engine and auth_configured and origins and (model_ids or ready_provider_ids))
        return jsonify(
            {
                "service": "generative-corrector-worker",
                "status": "ready" if configured else "degraded",
                "contract_version": CONTRACT_VERSION,
                "auth_configured": auth_configured,
                "origin_allowlist_configured": bool(origins),
                "engine_configured": configured_engine is not None,
                "model_ids": model_ids,
                "provider_ids": provider_ids,
                "ready_provider_ids": ready_provider_ids,
            }
        ), 200

    @app.post(CORRECTOR_ENDPOINT)
    def correct() -> tuple[Any, int]:
        if len(expected_token) < 24 or configured_engine is None or not origins:
            return jsonify(_uncorrelated_failure("worker_unavailable")), 503
        supplied = _bearer_token()
        if not supplied or not secrets.compare_digest(supplied, expected_token):
            response = jsonify(_uncorrelated_failure("unauthorized"))
            response.headers["WWW-Authenticate"] = "Bearer"
            return response, 401
        try:
            supplied_origin = _normalize_origin(str(request.headers.get("Origin") or ""))
        except ValueError:
            return jsonify(_uncorrelated_failure("hub_origin_forbidden")), 403
        if supplied_origin not in origins:
            return jsonify(_uncorrelated_failure("hub_origin_forbidden")), 403
        if not request.is_json:
            return jsonify(_uncorrelated_failure("invalid_content_type")), 415
        try:
            envelope = VoiceCorrectorWorkerRequest.from_dict(request.get_json(silent=True))
        except VoiceCorrectorContractError as exc:
            return jsonify(_uncorrelated_failure(exc.reason_code)), 400
        now_ms = time.time_ns() // 1_000_000
        remaining_seconds = (envelope.deadline_epoch_ms - now_ms) / 1000.0
        if remaining_seconds <= 0 or remaining_seconds > 120.0:
            return jsonify(_failure(envelope, "invalid_deadline")), 422
        if not _engine_supports_model(configured_engine, envelope.model_id):
            return jsonify(_failure(envelope, "model_not_allowlisted")), 422
        if not slots.acquire(timeout=remaining_seconds):
            return jsonify(_failure(envelope, "queue_full")), 429
        try:
            engine_result = configured_engine.correct(envelope)
            if engine_result.model_id != envelope.model_id:
                raise ValueError("corrector engine used a different model")
            corrected = engine_result.corrected_text
            if _protected_tokens(envelope.original_text) != _protected_tokens(corrected):
                return jsonify(_failure(envelope, "protected_token_changed")), 422
            edits = build_edits(envelope.original_text, corrected)
            if edit_ratio(envelope.original_text, edits) > envelope.max_edit_ratio + 1e-12:
                return jsonify(_failure(envelope, "edit_ratio_exceeded")), 422
            status = "unchanged" if corrected == envelope.original_text else "corrected"
            outcome = VoiceCorrectorWorkerResponse(
                request_id=envelope.request_id,
                task_id=envelope.task_id,
                status=status,
                original_text=envelope.original_text,
                corrected_text=corrected,
                edits=edits,
                reason_code=None,
                model_id=engine_result.model_id,
                model_revision=engine_result.model_revision,
                engine_id=engine_result.engine_id,
                prompt_version=engine_result.prompt_version,
            )
            outcome.validate_for(envelope)
            return jsonify(outcome.to_dict()), 200
        except TimeoutError:
            return jsonify(_failure(envelope, "corrector_engine_timeout")), 504
        except VoiceCorrectorContractError as exc:
            return jsonify(_failure(envelope, exc.reason_code)), 422
        except Exception:
            return jsonify(_failure(envelope, "corrector_engine_failed")), 503
        finally:
            slots.release()

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error: RequestEntityTooLarge) -> tuple[Any, int]:
        return jsonify(_uncorrelated_failure("request_too_large")), 413

    return app


def _engine_supports_model(engine: GenerativeCorrectorEngine, model_id: str) -> bool:
    supports = getattr(engine, "supports_model", None)
    return bool(supports(model_id)) if callable(supports) else model_id in engine.model_ids


def _engine_health_snapshot(
    engine: GenerativeCorrectorEngine | None,
) -> dict[str, tuple[str, ...]]:
    if engine is None:
        return {"model_ids": (), "provider_ids": (), "ready_provider_ids": ()}
    snapshot_reader = getattr(engine, "health_snapshot", None)
    if callable(snapshot_reader):
        snapshot = snapshot_reader()
        return {
            "model_ids": tuple(str(item) for item in snapshot.get("model_ids", ())),
            "provider_ids": tuple(str(item) for item in snapshot.get("provider_ids", ())),
            "ready_provider_ids": tuple(str(item) for item in snapshot.get("ready_provider_ids", ())),
        }
    return {
        "model_ids": tuple(str(item) for item in engine.model_ids),
        "provider_ids": tuple(str(item) for item in getattr(engine, "provider_ids", ())),
        "ready_provider_ids": tuple(str(item) for item in getattr(engine, "ready_provider_ids", ())),
    }


def _failure(envelope: VoiceCorrectorWorkerRequest, reason_code: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        VoiceCorrectorWorkerResponse(
            request_id=envelope.request_id,
            task_id=envelope.task_id,
            status="failed",
            original_text=envelope.original_text,
            corrected_text=None,
            edits=(),
            reason_code=reason_code,
            model_id=None,
            model_revision=None,
            engine_id=None,
            prompt_version=None,
        ).to_dict(),
    )


def _uncorrelated_failure(reason_code: str) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "failed",
        "reason_code": reason_code,
        "execution_owner": "worker",
    }


def _protected_tokens(value: str) -> Counter[str]:
    return Counter(match.group(0) for match in _PROTECTED_TOKEN_RE.finditer(value))


def _bearer_token() -> str:
    scheme, separator, token = str(request.headers.get("Authorization") or "").partition(" ")
    return token.strip() if separator and scheme.lower() == "bearer" else ""


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Hub origin is invalid")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit((parsed.scheme, f"{host}:{parsed.port}", "", "", ""))


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def main() -> None:
    host = str(os.getenv("GENERATIVE_CORRECTOR_HOST", "0.0.0.0"))
    port = _env_int("GENERATIVE_CORRECTOR_PORT", DEFAULT_PORT, minimum=1, maximum=65_535)
    create_app().run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
