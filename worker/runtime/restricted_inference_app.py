"""Internal HTTP entrypoint for the isolated restricted-inference worker.

Startup imports only the wire contract, snapshot verifier and worker handler;
it never constructs a model adapter.  Production wiring can inject a runtime
whose executor loads verified weights lazily inside this worker container.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from typing import Any, Mapping, Protocol

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.services.restricted_inference_contract import (
    CONTRACT_VERSION,
    RestrictedInferenceContractError,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    validate_response_for_request,
)
from agent.services.restricted_inference_model_manifest import VerifiedModelSnapshot
from worker.runtime.restricted_inference_runtime import RestrictedInferenceWorkerRuntime

DEFAULT_PORT = 8091
INFERENCE_ENDPOINT = "/internal/v1/restricted-inference"
MIN_BEARER_TOKEN_LENGTH = 24
STATUS_ENDPOINT = "/internal/v1/restricted-inference/status"
CACHE_GC_ENDPOINT = "/internal/v1/restricted-inference/cache/gc"
CONFIGURATION_ENDPOINT = "/internal/v1/restricted-inference/configuration"
LOAD_ENDPOINT = "/internal/v1/restricted-inference/models/load"
_DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,191}$")


class RestrictedInferenceRequestHandler(Protocol):
    def handle(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...


class _UnavailableSnapshotAdmission:
    def admit(self, manifest_id: str) -> VerifiedModelSnapshot:
        raise KeyError(manifest_id)


class _UnconfiguredExecutor:
    def execute(
        self,
        request_envelope: RestrictedInferenceRequest,
        snapshot: VerifiedModelSnapshot,
    ) -> Mapping[str, Any]:
        raise RuntimeError("restricted inference executor is not configured")


def _default_runtime() -> RestrictedInferenceWorkerRuntime:
    """Build a fail-closed handler without importing or loading an ML model."""

    return RestrictedInferenceWorkerRuntime(
        snapshot_admission=_UnavailableSnapshotAdmission(),
        executor=_UnconfiguredExecutor(),
    )


def _runtime_from_environment() -> RestrictedInferenceWorkerRuntime | None:
    """Create the real lazy runtime only when both local roots are configured."""

    manifest_root = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT", "")).strip()
    snapshot_root = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT", "")).strip()
    if not manifest_root or not snapshot_root:
        return None
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    from worker.runtime.restricted_inference_admission import FilesystemSnapshotAdmission
    from worker.runtime.restricted_inference_executor import build_default_executor
    from worker.runtime.restricted_inference_resources import ResourceBudget, ResourceLeaseManager

    budget = ResourceBudget(
        max_ram_bytes=_env_int("ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES", 8 * 1024**3, minimum=0),
        max_vram_bytes=_env_int("ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES", 0, minimum=0),
        max_loaded_models=_env_int("ANANTA_RESTRICTED_INFERENCE_MAX_LOADED_MODELS", 2, minimum=1, maximum=128),
        max_in_flight=_env_int("ANANTA_RESTRICTED_INFERENCE_MAX_IN_FLIGHT", 2, minimum=1, maximum=1024),
        max_queue=_env_int("ANANTA_RESTRICTED_INFERENCE_MAX_QUEUE", 8, minimum=0, maximum=100_000),
    )
    resources = ResourceLeaseManager(budget)
    executor, _registry = build_default_executor(
        resources=resources,
        cache_entries=_env_int("ANANTA_RESTRICTED_INFERENCE_CACHE_ENTRIES", 0, minimum=0, maximum=1_000_000),
        cache_ttl_seconds=_env_float(
            "ANANTA_RESTRICTED_INFERENCE_CACHE_TTL_SECONDS",
            300.0,
            minimum=1.0,
            maximum=86_400.0,
        ),
        allow_cpu_fallback=_env_bool("ANANTA_RESTRICTED_INFERENCE_ALLOW_CPU_FALLBACK", False),
        enabled_engines=_env_csv("RESTRICTED_INFERENCE_ENABLED_ENGINES"),
        worker_device=_env_device("RESTRICTED_INFERENCE_DEVICE", "cpu"),
    )
    admission = FilesystemSnapshotAdmission(
        manifest_root=manifest_root,
        snapshot_root=snapshot_root,
    )
    return RestrictedInferenceWorkerRuntime(
        snapshot_admission=admission,
        executor=executor,
        require_run_id=True,
    )


def _env_int(name: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_csv(name: str) -> frozenset[str] | None:
    """Return None only when no deployment upper bound was configured."""

    if name not in os.environ:
        return None
    return frozenset(item.strip().lower() for item in os.environ[name].split(",") if item.strip())


def _env_device(name: str, default: str) -> str:
    value = str(os.getenv(name, default)).strip().lower()
    if value not in {"cpu", "cuda"}:
        raise ValueError(f"{name} must be cpu or cuda")
    return value


def _error_payload(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "failed",
        "error": {"code": code, "message": message, "retryable": retryable},
        "no_generation": True,
    }


def _bearer_token() -> str:
    value = str(request.headers.get("Authorization") or "")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()


def _management_authorization(expected_token: str) -> tuple[Any, int] | None:
    if not expected_token:
        return jsonify(_error_payload("auth_not_configured", "worker authentication is not configured")), 503
    supplied_token = _bearer_token()
    if not supplied_token or not secrets.compare_digest(supplied_token, expected_token):
        response = jsonify(_error_payload("unauthorized", "valid bearer authentication is required"))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response, 401
    return None


def _http_status(response: Mapping[str, Any]) -> int:
    if response.get("status") == "succeeded":
        return 200
    error = response.get("error")
    code = str(error.get("code") or "") if isinstance(error, Mapping) else ""
    if code == "timeout":
        return 504
    if code == "queue_full":
        return 429
    if code in {
        "out_of_memory",
        "ram_budget_exhausted",
        "ram_unavailable",
        "vram_budget_exhausted",
        "vram_unavailable",
    }:
        return 507
    if code in {"manifest_unavailable", "unavailable", "worker_unavailable"}:
        return 503
    if code in {
        "generation_boundary_violation",
        "generation_field_forbidden",
        "hash_mismatch",
        "manifest_mismatch",
        "policy_blocked",
        "remote_code_forbidden",
        "result_provenance_mismatch",
        "run_id_required",
    }:
        return 422
    return 500


def _runtime_configuration(handler: Any) -> dict[str, Any]:
    read = getattr(handler, "configuration", None)
    if callable(read):
        return dict(read())
    return {
        "schema_version": "ananta.restricted-runtime-config.v1",
        "version": 1,
        "mutable": {},
        "fixed": {
            "downloads_allowed": False,
            "generation_allowed": False,
            "local_snapshots_only": True,
            "trust_remote_code": False,
        },
    }


def _runtime_configuration_update(handler: Any, body: object) -> tuple[dict[str, Any], int]:
    if not isinstance(body, dict) or set(body) != {"delta", "expected_version"}:
        return _error_payload("invalid_runtime_configuration", "configuration request is invalid"), 400
    delta = body.get("delta")
    expected_version = body.get("expected_version")
    if (
        not isinstance(delta, dict)
        or set(delta) != {"allow_cpu_fallback"}
        or not isinstance(delta.get("allow_cpu_fallback"), bool)
        or isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
    ):
        return _error_payload("invalid_runtime_configuration", "configuration request is invalid"), 422
    update = getattr(handler, "update_configuration", None)
    if not callable(update):
        return _error_payload("configuration_immutable", "runtime configuration is immutable"), 409
    try:
        return dict(update(delta, expected_version=expected_version)), 200
    except Exception as exc:
        code = str(getattr(exc, "reason_code", "") or "configuration_update_failed")
        status_code = 409 if code == "configuration_version_conflict" else 422
        return _error_payload(code, "runtime configuration update failed"), status_code


def _runtime_load(handler: Any, body: object) -> tuple[dict[str, Any], int]:
    if not isinstance(body, dict) or set(body) != {"deadline_epoch_ms", "manifest_id"}:
        return _error_payload("invalid_load_request", "model load request is invalid"), 400
    manifest_id = str(body.get("manifest_id") or "").strip()
    deadline_epoch_ms = body.get("deadline_epoch_ms")
    now_ms = time.time_ns() // 1_000_000
    if (
        not _MANIFEST_ID_RE.fullmatch(manifest_id)
        or isinstance(deadline_epoch_ms, bool)
        or not isinstance(deadline_epoch_ms, int)
        or not now_ms < deadline_epoch_ms <= now_ms + 300_000
    ):
        return _error_payload("invalid_load_request", "model load request is invalid"), 422
    load = getattr(handler, "load", None)
    if not callable(load):
        return _error_payload("load_unavailable", "explicit model load is unavailable"), 409
    try:
        model = dict(load(manifest_id, deadline_epoch_ms=deadline_epoch_ms))
    except KeyError:
        return _error_payload("manifest_unavailable", "model manifest is unavailable"), 404
    except Exception as exc:
        code = str(getattr(exc, "reason_code", "") or "model_load_failed")
        return _error_payload(code, "model load failed", retryable=True), 409
    return {"ok": True, "model": model, "no_generation": True}, 200


def create_app(
    *,
    runtime: RestrictedInferenceRequestHandler | None = None,
    auth_token: str | None = None,
    max_request_bytes: int | None = None,
) -> Flask:
    """Create the internal worker app with explicit injectable boundaries."""

    environment_runtime = None if runtime is not None else _runtime_from_environment()
    configured_runtime = runtime is not None or environment_runtime is not None
    handler = runtime or environment_runtime or _default_runtime()
    provided_token = str(
        auth_token if auth_token is not None else os.getenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", "")
    ).strip()
    expected_token = provided_token if len(provided_token) >= MIN_BEARER_TOKEN_LENGTH else ""
    limit = max_request_bytes
    if limit is None:
        try:
            limit = int(os.getenv("ANANTA_RESTRICTED_INFERENCE_MAX_REQUEST_BYTES", _DEFAULT_MAX_REQUEST_BYTES))
        except (TypeError, ValueError):
            limit = _DEFAULT_MAX_REQUEST_BYTES
    limit = max(1024, int(limit))

    app = Flask("ananta-restricted-inference-worker")
    app.config["MAX_CONTENT_LENGTH"] = limit

    @app.get("/health")
    def health() -> tuple[Any, int]:
        auth_configured = bool(expected_token)
        status = "ready" if auth_configured and configured_runtime else "degraded"
        return (
            jsonify(
                {
                    "service": "restricted-inference-worker",
                    "status": status,
                    "contract_version": CONTRACT_VERSION,
                    "auth_configured": auth_configured,
                    "runtime_configured": configured_runtime,
                }
            ),
            200,
        )

    @app.post(INFERENCE_ENDPOINT)
    def restricted_inference() -> tuple[Any, int]:
        if not expected_token:
            return jsonify(_error_payload("auth_not_configured", "worker authentication is not configured")), 503
        supplied_token = _bearer_token()
        if not supplied_token or not secrets.compare_digest(supplied_token, expected_token):
            response = jsonify(_error_payload("unauthorized", "valid bearer authentication is required"))
            response.headers["WWW-Authenticate"] = "Bearer"
            return response, 401
        if not request.is_json:
            return jsonify(_error_payload("invalid_content_type", "application/json is required")), 415
        envelope = request.get_json(silent=True)
        if not isinstance(envelope, dict):
            return jsonify(_error_payload("invalid_json", "request body must be a JSON object")), 400
        try:
            contract_request = RestrictedInferenceRequest.from_dict(envelope)
        except RestrictedInferenceContractError as exc:
            return jsonify(_error_payload(exc.reason_code, "request violates the inference contract")), 400
        try:
            response_payload = handler.handle(envelope)
        except RestrictedInferenceContractError as exc:
            return jsonify(_error_payload(exc.reason_code, "request violates the inference contract")), 400
        if not isinstance(response_payload, dict):
            return jsonify(_error_payload("invalid_runtime_response", "worker returned an invalid response")), 500
        try:
            contract_response = RestrictedInferenceResponse.from_dict(response_payload)
            validate_response_for_request(contract_request, contract_response)
        except RestrictedInferenceContractError:
            return jsonify(_error_payload("invalid_runtime_response", "worker returned an invalid response")), 500
        normalized_response = contract_response.to_dict()
        return jsonify(normalized_response), _http_status(normalized_response)

    @app.get(STATUS_ENDPOINT)
    def restricted_inference_status() -> tuple[Any, int]:
        unauthorized = _management_authorization(expected_token)
        if unauthorized is not None:
            return unauthorized
        status_reader = getattr(handler, "status", None)
        payload = status_reader() if callable(status_reader) else {"models": [], "resources": {}, "cache_entries": 0}
        return jsonify({"status": "ready" if configured_runtime else "degraded", **dict(payload)}), 200

    @app.get(CONFIGURATION_ENDPOINT)
    def restricted_inference_configuration() -> tuple[Any, int]:
        unauthorized = _management_authorization(expected_token)
        if unauthorized is not None:
            return unauthorized
        return jsonify(_runtime_configuration(handler)), 200

    @app.patch(CONFIGURATION_ENDPOINT)
    def restricted_inference_configuration_update() -> tuple[Any, int]:
        unauthorized = _management_authorization(expected_token)
        if unauthorized is not None:
            return unauthorized
        payload, status_code = _runtime_configuration_update(
            handler,
            request.get_json(silent=True) if request.is_json else None,
        )
        return jsonify(payload), status_code

    @app.post(LOAD_ENDPOINT)
    def restricted_inference_load() -> tuple[Any, int]:
        unauthorized = _management_authorization(expected_token)
        if unauthorized is not None:
            return unauthorized
        payload, status_code = _runtime_load(
            handler,
            request.get_json(silent=True) if request.is_json else None,
        )
        return jsonify(payload), status_code

    @app.post("/internal/v1/restricted-inference/models/<manifest_digest>/unload")
    def restricted_inference_unload(manifest_digest: str) -> tuple[Any, int]:
        unauthorized = _management_authorization(expected_token)
        if unauthorized is not None:
            return unauthorized
        if not _SHA256_RE.fullmatch(manifest_digest):
            return jsonify(_error_payload("invalid_manifest_digest", "manifest digest must be a SHA-256")), 400
        unload = getattr(handler, "unload", None)
        try:
            unloaded = bool(unload(manifest_digest)) if callable(unload) else False
        except Exception as exc:
            code = str(getattr(exc, "reason_code", "") or "unload_failed")
            return jsonify(_error_payload(code, "model unload failed", retryable=True)), 409
        return jsonify({"ok": True, "unloaded": unloaded, "manifest_digest": manifest_digest}), 200

    @app.post(CACHE_GC_ENDPOINT)
    def restricted_inference_cache_gc() -> tuple[Any, int]:
        unauthorized = _management_authorization(expected_token)
        if unauthorized is not None:
            return unauthorized
        clear = getattr(handler, "cache_gc", None)
        removed = int(clear()) if callable(clear) else 0
        return jsonify({"ok": True, "removed_entries": removed}), 200

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error: RequestEntityTooLarge) -> tuple[Any, int]:
        return jsonify(_error_payload("request_too_large", "request exceeds the configured size limit")), 413

    return app


def main() -> None:
    host = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_HOST", "0.0.0.0"))
    try:
        port = int(os.getenv("ANANTA_RESTRICTED_INFERENCE_PORT", str(DEFAULT_PORT)))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    create_app().run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
