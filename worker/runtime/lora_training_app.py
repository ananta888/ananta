"""Fail-closed HTTP control surface for the isolated LoRA-training worker."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn, Protocol

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from ananta_contracts.unsloth_capability import unavailable_worker_capability_probe
from worker.training.backends import (
    MockTrainingBackend,
    NeedleTrainingBackend,
    PeftTrlTrainingBackend,
    UnslothAudioTrainingBackend,
    UnslothEmbeddingTrainingBackend,
    UnslothTrainingBackend,
    UnslothVisionTrainingBackend,
)
from worker.training.backends.base import TrainingBackend
from worker.training.contracts import CONTRACT_VERSION, EVALUATION_JOB_TYPE, TrainingContractError
from worker.training.inference import (
    CONTRACT_VERSION as LORA_INFERENCE_CONTRACT_VERSION,
)
from worker.training.inference import (
    LoraInferenceRuntimeConfiguration,
    LoraInferenceWorkerError,
    LoraInferenceWorkerRuntime,
)
from worker.training.runtime import RuntimeConfiguration, TrainingRuntimeError, TrainingWorkerRuntime
from worker.training.storage_cleanup import WorkerStorageCleanupError

DEFAULT_PORT = 8095
MIN_BEARER_TOKEN_LENGTH = 24
JOBS_ENDPOINT = "/internal/v1/lora-training/jobs"
EVALUATIONS_ENDPOINT = "/internal/v1/lora-training/evaluations"
CAPABILITIES_ENDPOINT = "/internal/v1/lora-training/capabilities"
CLEANUP_ENDPOINT = "/internal/v1/lora-training/cleanup"
INFERENCE_CAPABILITIES_ENDPOINT = "/internal/v1/lora-training/inference/capabilities"
INFERENCE_GENERATE_ENDPOINT = "/internal/v1/lora-training/inference/generate"
INFERENCE_UNLOAD_ENDPOINT = "/internal/v1/lora-training/inference/adapters/<adapter_id>/<adapter_version>/unload"
_DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024


class RuntimePort(Protocol):
    def health(self) -> dict[str, Any]: ...

    def capability_probe(self) -> dict[str, Any]: ...

    def submit(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...

    def cleanup(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...

    def status(self, job_id: str) -> dict[str, Any]: ...

    def heartbeat(self, job_id: str) -> dict[str, Any]: ...

    def events(self, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> dict[str, Any]: ...

    def artifact(self, job_id: str, artifact_name: str) -> tuple[Path, dict[str, Any]]: ...


class LoraInferenceRuntimePort(Protocol):
    def capabilities(self) -> dict[str, Any]: ...

    def generate(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...

    def unload(self, *, adapter_id: str, adapter_version: str) -> dict[str, Any]: ...


class _UnavailableRuntime:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def health(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "degraded",
            "runtime_configured": False,
            "backends": {},
            "errors": [self._reason],
        }

    def capability_probe(self) -> dict[str, Any]:
        return unavailable_worker_capability_probe(
            contract_version=CONTRACT_VERSION,
            reason_code="runtime_not_configured",
        )

    def _reject(self) -> NoReturn:
        raise TrainingRuntimeError(
            "worker_degraded", "training runtime is not configured", http_status=503, retryable=True
        )

    def submit(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        self._reject()

    def cleanup(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        self._reject()

    def status(self, job_id: str) -> dict[str, Any]:
        self._reject()

    def heartbeat(self, job_id: str) -> dict[str, Any]:
        self._reject()

    def events(self, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        self._reject()

    def cancel(self, job_id: str) -> dict[str, Any]:
        self._reject()

    def artifact(self, job_id: str, artifact_name: str) -> tuple[Path, dict[str, Any]]:
        self._reject()


def _runtime_from_environment() -> RuntimePort:
    root_names = {
        "state_root": "ANANTA_LORA_TRAINING_STATE_ROOT",
        "workspace_root": "ANANTA_LORA_TRAINING_WORKSPACE_ROOT",
        "dataset_root": "ANANTA_LORA_TRAINING_DATASET_ROOT",
        "model_root": "ANANTA_LORA_TRAINING_MODEL_ROOT",
    }
    raw_roots = {key: str(os.getenv(env_name, "")).strip() for key, env_name in root_names.items()}
    missing = [root_names[key] for key, value in raw_roots.items() if not value]
    if missing:
        return _UnavailableRuntime("missing runtime roots: " + ", ".join(missing))

    enabled = _env_csv("ANANTA_LORA_TRAINING_BACKENDS", "mock")
    factories: dict[str, Callable[[], TrainingBackend]] = {
        "mock": MockTrainingBackend,
        "needle": NeedleTrainingBackend,
        "peft_trl": PeftTrlTrainingBackend,
        "unsloth": UnslothTrainingBackend,
        "unsloth_audio": UnslothAudioTrainingBackend,
        "unsloth_embedding": UnslothEmbeddingTrainingBackend,
        "unsloth_vision": UnslothVisionTrainingBackend,
    }
    unknown = sorted(enabled.difference(factories))
    if unknown:
        return _UnavailableRuntime("unknown training backend(s): " + ", ".join(unknown))
    backends = {name: factories[name]() for name in sorted(enabled)}
    config = RuntimeConfiguration(
        state_root=Path(raw_roots["state_root"]),
        workspace_root=Path(raw_roots["workspace_root"]),
        dataset_root=Path(raw_roots["dataset_root"]),
        model_root=Path(raw_roots["model_root"]),
        resource_profile=str(os.getenv("ANANTA_LORA_TRAINING_RESOURCE_PROFILE", "mock")).strip().lower(),
        max_workers=_env_int("ANANTA_LORA_TRAINING_MAX_WORKERS", 1, minimum=1, maximum=128),
        max_queue=_env_int("ANANTA_LORA_TRAINING_MAX_QUEUE", 2, minimum=0, maximum=10_000),
        max_dataset_bytes=_env_int(
            "ANANTA_LORA_TRAINING_MAX_DATASET_BYTES",
            4 * 1024**3,
            minimum=1,
            maximum=1024**5,
        ),
        max_dataset_records=_env_int(
            "ANANTA_LORA_TRAINING_MAX_DATASET_RECORDS",
            10_000_000,
            minimum=1,
            maximum=100_000_000,
        ),
        max_model_bytes=_env_int(
            "ANANTA_LORA_TRAINING_MAX_MODEL_BYTES",
            32 * 1024**3,
            minimum=1,
            maximum=1024**5,
        ),
        max_checkpoint_bytes=_env_int(
            "ANANTA_LORA_TRAINING_MAX_CHECKPOINT_BYTES",
            16 * 1024**3,
            minimum=1,
            maximum=1024**5,
        ),
        max_export_bytes=_env_int(
            "ANANTA_LORA_TRAINING_MAX_EXPORT_BYTES",
            16 * 1024**3,
            minimum=1,
            maximum=1024**5,
        ),
        max_tenant_bytes=_env_int(
            "ANANTA_LORA_TRAINING_MAX_TENANT_STORAGE_BYTES",
            64 * 1024**3,
            minimum=1,
            maximum=1024**5,
        ),
        isolate_processes=_env_bool("ANANTA_LORA_TRAINING_ISOLATE_PROCESSES", True),
        termination_grace_seconds=float(
            _env_int("ANANTA_LORA_TRAINING_TERMINATION_GRACE_SECONDS", 15, minimum=1, maximum=300)
        ),
    )
    return TrainingWorkerRuntime(config, backends)


class _UnavailableLoraInferenceRuntime:
    def __init__(self, reason_code: str, detail: str) -> None:
        self._reason_code = reason_code
        self._detail = detail

    def capabilities(self) -> dict[str, Any]:
        return {
            "contract_version": LORA_INFERENCE_CONTRACT_VERSION,
            "status": "degraded",
            "available": False,
            "reason_code": self._reason_code,
            "detail": self._detail,
            "resource_profile": str(os.getenv("ANANTA_LORA_TRAINING_RESOURCE_PROFILE", "mock")),
            "capabilities": [],
            "limits": {},
        }

    def generate(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        del envelope
        raise LoraInferenceWorkerError(
            self._reason_code,
            "LoRA inference runtime is unavailable",
            http_status=503,
            retryable=True,
        )

    def unload(self, *, adapter_id: str, adapter_version: str) -> dict[str, Any]:
        del adapter_id, adapter_version
        raise LoraInferenceWorkerError(
            self._reason_code,
            "LoRA inference runtime is unavailable",
            http_status=503,
            retryable=True,
        )


def _inference_runtime_from_environment() -> LoraInferenceRuntimePort:
    workspace_root = str(os.getenv("ANANTA_LORA_TRAINING_WORKSPACE_ROOT", "")).strip()
    model_root = str(os.getenv("ANANTA_LORA_TRAINING_MODEL_ROOT", "")).strip()
    resource_profile = str(os.getenv("ANANTA_LORA_TRAINING_RESOURCE_PROFILE", "mock")).strip().lower()
    if resource_profile not in {"cpu", "nvidia"}:
        return _UnavailableLoraInferenceRuntime(
            "lora_inference_profile_unavailable",
            "LoRA inference is available only in cpu or nvidia worker profiles",
        )
    if not workspace_root or not model_root:
        return _UnavailableLoraInferenceRuntime(
            "lora_inference_roots_missing",
            "LoRA inference workspace or model root is not configured",
        )
    try:
        return LoraInferenceWorkerRuntime(
            LoraInferenceRuntimeConfiguration(
                workspace_root=Path(workspace_root),
                model_root=Path(model_root),
                resource_profile=resource_profile,
                max_loaded_adapters=_env_int(
                    "ANANTA_LORA_INFERENCE_MAX_LOADED_ADAPTERS",
                    1,
                    minimum=1,
                    maximum=16,
                ),
                max_prompt_chars=_env_int(
                    "ANANTA_LORA_INFERENCE_MAX_PROMPT_CHARS",
                    1_048_576,
                    minimum=1_024,
                    maximum=8_388_608,
                ),
                max_response_chars=_env_int(
                    "ANANTA_LORA_INFERENCE_MAX_RESPONSE_CHARS",
                    4_194_304,
                    minimum=1_024,
                    maximum=16_777_216,
                ),
                max_adapter_bytes=_env_int(
                    "ANANTA_LORA_INFERENCE_MAX_ADAPTER_BYTES",
                    2 * 1024**3,
                    minimum=1_024,
                    maximum=64 * 1024**3,
                ),
            )
        )
    except (OSError, ValueError) as exc:
        return _UnavailableLoraInferenceRuntime(
            "lora_inference_configuration_invalid",
            type(exc).__name__,
        )


def create_app(  # noqa: C901 - additive Flask route composition remains explicit
    *,
    runtime: RuntimePort | None = None,
    inference_runtime: LoraInferenceRuntimePort | None = None,
    auth_token: str | None = None,
    max_request_bytes: int | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_request_bytes or _env_int(
        "ANANTA_LORA_TRAINING_MAX_REQUEST_BYTES",
        _DEFAULT_MAX_REQUEST_BYTES,
        minimum=1024,
        maximum=64 * 1024**2,
    )
    worker_runtime = runtime or _runtime_from_environment()
    lora_inference_runtime = inference_runtime or _inference_runtime_from_environment()
    configured_token = (
        str(auth_token).strip() if auth_token is not None else str(os.getenv("ANANTA_LORA_TRAINING_TOKEN", "")).strip()
    )
    token_ready = len(configured_token) >= MIN_BEARER_TOKEN_LENGTH
    app.extensions["lora_training_runtime"] = worker_runtime
    app.extensions["lora_inference_runtime"] = lora_inference_runtime

    @app.get("/health")
    def health() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        payload = worker_runtime.health()
        payload["inference"] = lora_inference_runtime.capabilities()
        payload["auth_configured"] = token_ready
        if not token_ready:
            payload["status"] = "degraded"
        return jsonify(payload), 200

    @app.get(CAPABILITIES_ENDPOINT)
    def capabilities() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        probe = getattr(worker_runtime, "capability_probe", None)
        if not callable(probe):
            return jsonify(
                unavailable_worker_capability_probe(
                    contract_version=CONTRACT_VERSION,
                    reason_code="runtime_not_configured",
                )
            ), 200
        return jsonify(probe()), 200

    @app.post(JOBS_ENDPOINT)
    def submit_job() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        payload, invalid = _request_mapping()
        if invalid:
            return invalid
        assert payload is not None
        try:
            return jsonify(worker_runtime.submit(payload)), 202
        except (TrainingRuntimeError, TrainingContractError) as exc:
            return _domain_error(exc)

    @app.post(CLEANUP_ENDPOINT)
    def cleanup_storage() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        payload, invalid = _request_mapping()
        if invalid:
            return invalid
        assert payload is not None
        try:
            return jsonify(worker_runtime.cleanup(payload)), 200
        except WorkerStorageCleanupError as exc:
            return _error(
                str(
                    getattr(
                        exc,
                        "reason_code",
                        "storage_cleanup_rejected",
                    )
                ),
                str(exc),
                int(getattr(exc, "http_status", 422)),
            )

    @app.get(INFERENCE_CAPABILITIES_ENDPOINT)
    def inference_capabilities() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        return jsonify(lora_inference_runtime.capabilities()), 200

    @app.post(INFERENCE_GENERATE_ENDPOINT)
    def inference_generate() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        payload, invalid = _request_mapping()
        if invalid:
            return invalid
        assert payload is not None
        try:
            return jsonify(lora_inference_runtime.generate(payload)), 200
        except LoraInferenceWorkerError as exc:
            return _inference_error(exc)

    @app.post(INFERENCE_UNLOAD_ENDPOINT)
    def inference_unload(adapter_id: str, adapter_version: str) -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        payload, invalid = _request_mapping()
        if invalid:
            return invalid
        assert payload is not None
        if set(payload) != {"confirmed", "reason"} or payload.get("confirmed") is not True:
            return _error("inference_unload_confirmation_required", "confirmed unload is required", 422)
        reason = str(payload.get("reason") or "").strip()
        if len(reason) < 10 or len(reason) > 512:
            return _error("inference_unload_reason_invalid", "a bounded unload reason is required", 422)
        try:
            return jsonify(
                lora_inference_runtime.unload(
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                )
            ), 200
        except LoraInferenceWorkerError as exc:
            return _inference_error(exc)

    @app.post(EVALUATIONS_ENDPOINT)
    def submit_evaluation() -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        payload, invalid = _request_mapping()
        if invalid:
            return invalid
        assert payload is not None
        if payload.get("job_type") != EVALUATION_JOB_TYPE:
            return _error(
                "unsupported_job_type",
                f"{EVALUATIONS_ENDPOINT} requires job_type {EVALUATION_JOB_TYPE}",
                422,
            )
        try:
            return jsonify(worker_runtime.submit(payload)), 202
        except (TrainingRuntimeError, TrainingContractError) as exc:
            return _domain_error(exc)

    @app.get(f"{JOBS_ENDPOINT}/<job_id>")
    def job_status(job_id: str) -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        try:
            return jsonify(worker_runtime.status(job_id)), 200
        except TrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.post(f"{JOBS_ENDPOINT}/<job_id>/heartbeat")
    def job_heartbeat(job_id: str) -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        try:
            return jsonify(worker_runtime.heartbeat(job_id)), 200
        except TrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.get(f"{JOBS_ENDPOINT}/<job_id>/events")
    def job_events(job_id: str) -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        try:
            after_sequence = int(request.args.get("after_sequence", "0"))
            limit = int(request.args.get("limit", "100"))
            return jsonify(worker_runtime.events(job_id, after_sequence=after_sequence, limit=limit)), 200
        except ValueError:
            return _error("invalid_pagination", "event cursor and limit must be integers", 422)
        except TrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.post(f"{JOBS_ENDPOINT}/<job_id>/cancel")
    def cancel_job(job_id: str) -> tuple[Any, int]:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        try:
            payload = worker_runtime.cancel(job_id)
            return jsonify(payload), 200 if payload["status"] in {"succeeded", "failed", "cancelled"} else 202
        except TrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.get(f"{JOBS_ENDPOINT}/<job_id>/artifacts/<path:artifact_name>")
    def download_artifact(job_id: str, artifact_name: str) -> Any:
        rejection = _authorization(configured_token, token_ready)
        if rejection:
            return rejection
        try:
            path, metadata = worker_runtime.artifact(job_id, artifact_name)
            response = send_file(
                path,
                mimetype=metadata["media_type"],
                as_attachment=True,
                download_name=Path(artifact_name).name,
                conditional=True,
            )
            response.headers["X-Artifact-SHA256"] = metadata["sha256"]
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except TrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(exc: RequestEntityTooLarge) -> tuple[Any, int]:
        return _error("request_too_large", "request exceeds the configured byte limit", 413)

    return app


def _domain_error(exc: TrainingRuntimeError | TrainingContractError) -> tuple[Any, int]:
    return _error(exc.code, exc.message, exc.http_status, retryable=exc.retryable)


def _inference_error(exc: LoraInferenceWorkerError) -> tuple[Any, int]:
    return _error(
        exc.reason_code,
        str(exc),
        exc.http_status,
        retryable=exc.retryable,
        contract_version=LORA_INFERENCE_CONTRACT_VERSION,
    )


def _authorization(configured_token: str, token_ready: bool) -> tuple[Any, int] | None:
    if not token_ready:
        return _error("auth_not_configured", "worker authentication is not configured", 503)
    value = str(request.headers.get("Authorization") or "")
    scheme, separator, supplied = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not secrets.compare_digest(supplied.strip(), configured_token):
        response, status = _error("unauthorized", "valid bearer authentication is required", 401)
        response.headers["WWW-Authenticate"] = "Bearer"
        return response, status
    return None


def _request_mapping() -> tuple[Mapping[str, Any] | None, tuple[Any, int] | None]:
    if not request.is_json:
        return None, _error("unsupported_media_type", "application/json is required", 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        return None, _error("invalid_json", "request body must be a JSON object", 400)
    return payload, None


def _error(
    code: str,
    message: str,
    status: int,
    *,
    retryable: bool = False,
    contract_version: str = CONTRACT_VERSION,
) -> tuple[Any, int]:
    return (
        jsonify(
            {
                "contract_version": contract_version,
                "status": "failed",
                "error": {"code": code, "message": message, "retryable": retryable},
            }
        ),
        status,
    )


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_csv(name: str, default: str) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in str(os.getenv(name, default)).split(",") if item.strip())


def _env_bool(name: str, default: bool) -> bool:
    value = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def main() -> None:
    app = create_app()
    app.run(
        host=str(os.getenv("ANANTA_LORA_TRAINING_HOST", "0.0.0.0")),
        port=_env_int("ANANTA_LORA_TRAINING_PORT", DEFAULT_PORT, minimum=1, maximum=65_535),
        threaded=True,
    )


if __name__ == "__main__":
    main()
