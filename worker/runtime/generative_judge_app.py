"""Authenticated bounded HTTP entrypoint for the generative-judge worker."""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Iterable
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ananta_contracts.generative_judge_worker import (
    CONTRACT_VERSION,
    GenerativeJudgeContractError,
    GenerativeJudgeWorkerRequest,
    GenerativeJudgeWorkerResponse,
)
from worker.runtime.generative_judge_engine import (
    EmbeddedTransformersGenerativeJudgeEngine,
    GenerativeJudgeEngine,
    LoopbackGenerativeJudgeEngine,
)

DEFAULT_PORT = 8092
JUDGE_ENDPOINT = "/internal/v1/generative-judge"
_DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024


def _engine_from_environment() -> GenerativeJudgeEngine | None:
    mode = str(os.getenv("GENERATIVE_JUDGE_ENGINE", "")).strip().lower()
    if mode == "loopback":
        endpoint = str(os.getenv("GENERATIVE_JUDGE_LOOPBACK_ENDPOINT", "")).strip()
        if not endpoint:
            return None
        return LoopbackGenerativeJudgeEngine(
            endpoint=endpoint,
            bearer_token=str(os.getenv("GENERATIVE_JUDGE_LOOPBACK_TOKEN", "")),
            max_response_bytes=_env_int(
                "GENERATIVE_JUDGE_ENGINE_MAX_RESPONSE_BYTES",
                64 * 1024,
                minimum=1024,
                maximum=1024 * 1024,
            ),
        )
    if mode == "transformers":
        model_root = str(os.getenv("GENERATIVE_JUDGE_MODEL_ROOT", "")).strip()
        model_path = str(os.getenv("GENERATIVE_JUDGE_MODEL_PATH", "")).strip()
        if not model_root or not model_path:
            return None
        return EmbeddedTransformersGenerativeJudgeEngine(
            model_path=model_path,
            model_root=model_root,
            device=str(os.getenv("GENERATIVE_JUDGE_DEVICE", "cpu")).strip().lower(),
            max_input_chars=_env_int(
                "GENERATIVE_JUDGE_MAX_INPUT_CHARS",
                64_000,
                minimum=1024,
                maximum=512_000,
            ),
            max_input_tokens=_env_int(
                "GENERATIVE_JUDGE_MAX_INPUT_TOKENS",
                4_096,
                minimum=128,
                maximum=32_768,
            ),
            max_new_tokens=_env_int(
                "GENERATIVE_JUDGE_MAX_NEW_TOKENS",
                32,
                minimum=1,
                maximum=128,
            ),
        )
    return None


def create_app(
    *,
    engine: GenerativeJudgeEngine | None = None,
    auth_token: str | None = None,
    allowed_hub_origins: Iterable[str] | None = None,
    max_request_bytes: int | None = None,
    max_in_flight: int | None = None,
) -> Flask:
    configured_engine = engine if engine is not None else _engine_from_environment()
    expected_token = str(
        auth_token if auth_token is not None else os.getenv("GENERATIVE_JUDGE_INTERNAL_TOKEN", "")
    ).strip()
    raw_origins = (
        tuple(allowed_hub_origins)
        if allowed_hub_origins is not None
        else tuple(
            item.strip()
            for item in str(os.getenv("GENERATIVE_JUDGE_ALLOWED_HUB_ORIGINS", "")).split(",")
            if item.strip()
        )
    )
    origins = frozenset(_normalize_origin(item) for item in raw_origins)
    request_limit = (
        int(max_request_bytes)
        if max_request_bytes is not None
        else _env_int(
            "GENERATIVE_JUDGE_MAX_REQUEST_BYTES",
            _DEFAULT_MAX_REQUEST_BYTES,
            minimum=1024,
            maximum=4 * 1024 * 1024,
        )
    )
    concurrency = (
        int(max_in_flight)
        if max_in_flight is not None
        else _env_int(
            "GENERATIVE_JUDGE_MAX_IN_FLIGHT",
            1,
            minimum=1,
            maximum=32,
        )
    )
    if not 1024 <= request_limit <= 4 * 1024 * 1024:
        raise ValueError("generative judge request limit is outside its bounds")
    if not 1 <= concurrency <= 32:
        raise ValueError("generative judge concurrency is outside its bounds")
    slots = threading.BoundedSemaphore(concurrency)

    app = Flask("ananta-generative-judge-worker")
    app.config["MAX_CONTENT_LENGTH"] = int(request_limit)

    @app.get("/health")
    def health() -> tuple[Any, int]:
        auth_configured = len(expected_token) >= 24
        configured = bool(configured_engine and auth_configured and origins)
        return jsonify(
            {
                "service": "generative-judge-worker",
                "status": "ready" if configured else "degraded",
                "contract_version": CONTRACT_VERSION,
                "auth_configured": auth_configured,
                "origin_allowlist_configured": bool(origins),
                "engine_configured": configured_engine is not None,
            }
        ), 200

    @app.post(JUDGE_ENDPOINT)
    def judge() -> tuple[Any, int]:
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
            envelope = GenerativeJudgeWorkerRequest.from_dict(request.get_json(silent=True))
        except GenerativeJudgeContractError as exc:
            return jsonify(_uncorrelated_failure(exc.reason_code)), 400
        now_ms = time.time_ns() // 1_000_000
        remaining_seconds = (envelope.deadline_epoch_ms - now_ms) / 1000.0
        if remaining_seconds <= 0 or remaining_seconds > 60.0:
            return jsonify(_failure(envelope, "invalid_deadline")), 422
        if not slots.acquire(timeout=remaining_seconds):
            return jsonify(_failure(envelope, "queue_full")), 429
        try:
            choice_id = configured_engine.select(envelope)
            if choice_id not in {candidate.choice_id for candidate in envelope.candidates}:
                raise ValueError("engine selected an unknown candidate")
            worker_outcome = GenerativeJudgeWorkerResponse(
                request_id=envelope.request_id,
                task_id=envelope.task_id,
                status="selected",
                choice_id=choice_id,
                reason_code=None,
                engine_id=configured_engine.engine_id,
            )
            return jsonify(worker_outcome.to_dict()), 200
        except TimeoutError:
            return jsonify(_failure(envelope, "judge_engine_timeout")), 504
        except Exception:
            return jsonify(_failure(envelope, "judge_engine_failed")), 503
        finally:
            slots.release()

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error: RequestEntityTooLarge) -> tuple[Any, int]:
        return jsonify(_uncorrelated_failure("request_too_large")), 413

    return app


def _failure(request_envelope: GenerativeJudgeWorkerRequest, reason_code: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        GenerativeJudgeWorkerResponse(
            request_id=request_envelope.request_id,
            task_id=request_envelope.task_id,
            status="failed",
            choice_id=None,
            reason_code=reason_code,
            engine_id=None,
        ).to_dict(),
    )


def _uncorrelated_failure(reason_code: str) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "failed",
        "reason_code": reason_code,
        "execution_owner": "worker",
    }


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
    host = str(os.getenv("GENERATIVE_JUDGE_HOST", "0.0.0.0"))
    port = _env_int("GENERATIVE_JUDGE_PORT", DEFAULT_PORT, minimum=1, maximum=65535)
    create_app().run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
