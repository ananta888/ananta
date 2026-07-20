"""Fail-closed HTTP surface for the isolated speech-training worker."""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    SpeechAdaptationContractError,
    SpeechAdaptationJob,
)
from worker.speech_training.backend import AbortSignal, SpeechDatasetView
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.backends import MockSpeechTrainingBackend
from worker.speech_training.hub_ports import (
    HttpHubSpeechTrainingPorts,
    HubValidatedMockDatasetResolver,
)
from worker.speech_training.result_publisher import PublicationReceipt, SpeechResultPublisher
from worker.speech_training.runner import SpeechTrainingRunner

BASE_PATH = "/internal/v1/speech-training"
MIN_TOKEN_LENGTH = 24
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024


class SpeechTrainingRuntimeError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable


class SpeechTrainingRuntimePort(Protocol):
    def health(self) -> Mapping[str, Any]: ...

    def readiness(self) -> Mapping[str, Any]: ...

    def submit(self, job: SpeechAdaptationJob) -> Mapping[str, Any]: ...

    def status(self, job_id: str) -> Mapping[str, Any]: ...

    def cancel(
        self,
        job_id: str,
        *,
        attempt_id: str,
        fencing_digest: str,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def drain(self) -> Mapping[str, Any]: ...


@dataclass
class _RuntimeJob:
    job: SpeechAdaptationJob
    abort: AbortSignal
    status: str = "accepted"
    result: dict[str, Any] | None = None


class SpeechTrainingRuntime:
    """One-process runtime; the Hub remains the sole orchestration owner."""

    def __init__(self, runner: SpeechTrainingRunner, *, max_workers: int = 1) -> None:
        if not 1 <= max_workers <= 8:
            raise ValueError("speech worker concurrency is outside its bounds")
        self._runner = runner
        self._max_workers = max_workers
        self._jobs: dict[str, _RuntimeJob] = {}
        self._lock = threading.RLock()
        self._draining = False

    def health(self) -> Mapping[str, Any]:
        with self._lock:
            active = sum(item.status in {"accepted", "running", "cancel_requested"} for item in self._jobs.values())
            return {
                "contract_version": CONTRACT_VERSION,
                "status": "ok",
                "active_jobs": active,
                "draining": self._draining,
            }

    def readiness(self) -> Mapping[str, Any]:
        with self._lock:
            active = sum(item.status in {"accepted", "running", "cancel_requested"} for item in self._jobs.values())
            ready = not self._draining and active < self._max_workers
            return {
                "contract_version": CONTRACT_VERSION,
                "ready": ready,
                "reason_code": None if ready else "speech_worker_draining" if self._draining else "speech_worker_busy",
            }

    def submit(self, job: SpeechAdaptationJob) -> Mapping[str, Any]:
        with self._lock:
            existing = self._jobs.get(job.job_id)
            if existing is not None:
                if (
                    existing.job.attempt.attempt_id != job.attempt.attempt_id
                    or existing.job.binding_digest != job.binding_digest
                    or existing.job.fencing.fencing_digest != job.fencing.fencing_digest
                ):
                    raise SpeechTrainingRuntimeError(
                        "speech_job_id_conflict",
                        "speech job ID is already bound to another attempt",
                        status_code=409,
                    )
                return _submission(existing)
            if self._draining:
                raise SpeechTrainingRuntimeError(
                    "speech_worker_draining",
                    "speech worker is draining",
                    status_code=503,
                    retryable=True,
                )
            active = sum(item.status in {"accepted", "running", "cancel_requested"} for item in self._jobs.values())
            if active >= self._max_workers:
                raise SpeechTrainingRuntimeError(
                    "speech_worker_capacity_exhausted",
                    "speech worker has no admitted capacity",
                    status_code=503,
                    retryable=True,
                )
            state = _RuntimeJob(job=job, abort=AbortSignal())
            self._jobs[job.job_id] = state
            thread = threading.Thread(
                target=self._execute,
                args=(state,),
                name=f"speech-training-{job.job_id[:32]}",
                daemon=True,
            )
            thread.start()
            return _submission(state)

    def status(self, job_id: str) -> Mapping[str, Any]:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                raise SpeechTrainingRuntimeError("speech_job_not_found", "speech job was not found", status_code=404)
            return {
                "contract_version": CONTRACT_VERSION,
                "job_id": state.job.job_id,
                "attempt_id": state.job.attempt.attempt_id,
                "status": state.status,
                "result": dict(state.result) if state.result is not None else None,
            }

    def cancel(
        self,
        job_id: str,
        *,
        attempt_id: str,
        fencing_digest: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                raise SpeechTrainingRuntimeError("speech_job_not_found", "speech job was not found", status_code=404)
            if state.job.attempt.attempt_id != attempt_id or not secrets.compare_digest(
                state.job.fencing.fencing_digest, fencing_digest
            ):
                raise SpeechTrainingRuntimeError(
                    "speech_cancel_fencing_mismatch",
                    "speech cancellation is stale",
                    status_code=409,
                )
            if state.status in {"completed", "dataset_only", "cancelled", "failed"}:
                return {"job_id": job_id, "status": state.status}
            state.status = "cancel_requested"
            state.abort.abort(reason_code)
            return {"job_id": job_id, "status": "cancel_requested"}

    def drain(self) -> Mapping[str, Any]:
        with self._lock:
            self._draining = True
        return {"status": "draining"}

    def _execute(self, state: _RuntimeJob) -> None:
        with self._lock:
            if state.status == "accepted":
                state.status = "running"
        result = self._runner.run(state.job, abort=state.abort)
        with self._lock:
            state.result = result.to_dict()
            state.status = result.status


class _UnavailableRuntime:
    def health(self) -> Mapping[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "degraded",
            "active_jobs": 0,
            "draining": False,
        }

    def readiness(self) -> Mapping[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "ready": False,
            "reason_code": "speech_worker_not_configured",
        }

    def _raise(self) -> None:
        raise SpeechTrainingRuntimeError(
            "speech_worker_not_configured",
            "speech worker runtime is not configured",
            status_code=503,
            retryable=True,
        )

    def submit(self, job: SpeechAdaptationJob) -> Mapping[str, Any]:
        del job
        self._raise()

    def status(self, job_id: str) -> Mapping[str, Any]:
        del job_id
        self._raise()

    def cancel(
        self,
        job_id: str,
        *,
        attempt_id: str,
        fencing_digest: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        del job_id, attempt_id, fencing_digest, reason_code
        self._raise()

    def drain(self) -> Mapping[str, Any]:
        return {"status": "draining"}


def create_app(
    *,
    runtime: SpeechTrainingRuntimePort | None = None,
    auth_token: str | None = None,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_request_bytes
    worker = runtime or _runtime_from_environment()
    token = str(auth_token if auth_token is not None else os.getenv("ANANTA_SPEECH_TRAINING_TOKEN", "")).strip()
    token_ready = len(token) >= MIN_TOKEN_LENGTH and not any(character.isspace() for character in token)

    @app.before_request
    def authorize():
        if not token_ready:
            return _error("speech_worker_auth_not_configured", 503, retryable=True)
        supplied = str(request.headers.get("Authorization") or "")
        expected = f"Bearer {token}"
        if not secrets.compare_digest(supplied, expected):
            return _error("speech_worker_unauthorized", 401)
        if request.path.startswith(BASE_PATH):
            version = str(request.headers.get("X-Ananta-Contract-Version") or "")
            if version != CONTRACT_VERSION:
                return _error("speech_contract_version_unsupported", 422)
        return None

    @app.get("/health")
    def health():
        return jsonify(dict(worker.health())), 200

    @app.get("/ready")
    def readiness():
        payload = dict(worker.readiness())
        return jsonify(payload), 200 if payload.get("ready") else 503

    @app.post(f"{BASE_PATH}/jobs")
    def submit():
        try:
            payload = _json_body()
            job = SpeechAdaptationJob.from_mapping(payload)
            return jsonify(dict(worker.submit(job))), 202
        except (SpeechAdaptationContractError, SpeechTrainingRuntimeError) as exc:
            return _domain_error(exc)

    @app.get(f"{BASE_PATH}/jobs/<job_id>")
    def status(job_id: str):
        try:
            return jsonify(dict(worker.status(job_id))), 200
        except SpeechTrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.post(f"{BASE_PATH}/jobs/<job_id>/cancel")
    def cancel(job_id: str):
        try:
            payload = _json_body()
            if set(payload) != {"attempt_id", "fencing_digest", "reason_code"}:
                raise SpeechTrainingRuntimeError("speech_cancel_shape_invalid", "speech cancel request is invalid")
            result = worker.cancel(
                job_id,
                attempt_id=str(payload.get("attempt_id") or ""),
                fencing_digest=str(payload.get("fencing_digest") or ""),
                reason_code=str(payload.get("reason_code") or ""),
            )
            return jsonify(dict(result)), 202
        except SpeechTrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.post(f"{BASE_PATH}/drain")
    def drain():
        try:
            payload = _json_body()
            if payload != {"drain": True}:
                raise SpeechTrainingRuntimeError("speech_drain_confirmation_required", "explicit drain is required")
            return jsonify(dict(worker.drain())), 202
        except SpeechTrainingRuntimeError as exc:
            return _domain_error(exc)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc: RequestEntityTooLarge):
        return _error("speech_worker_request_too_large", 413)

    return app


def _runtime_from_environment() -> SpeechTrainingRuntimePort:
    """Enable only the deterministic, audio-free CI runtime implicitly.

    Production runtime composition must inject authenticated Hub artifact,
    dataset and authority ports.  This prevents a worker from silently
    treating a shared path or its own state as Hub admission.
    """

    enabled = str(os.getenv("ANANTA_SPEECH_TRAINING_CI_MOCK", "")).strip().casefold()
    if enabled not in {"1", "true", "yes", "on"}:
        return _UnavailableRuntime()
    root = Path(os.getenv("ANANTA_SPEECH_TRAINING_WORKSPACE_ROOT", "/work")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    callback_endpoint = str(os.getenv("ANANTA_SPEECH_TRAINING_HUB_CALLBACK_URL", "")).strip()
    callback_allowed = tuple(
        item.strip()
        for item in str(os.getenv("ANANTA_SPEECH_TRAINING_HUB_ALLOWED_ENDPOINTS", "")).split(",")
        if item.strip()
    )
    callback_token = str(os.getenv("ANANTA_SPEECH_TRAINING_CALLBACK_TOKEN", "")).strip()
    if callback_endpoint or callback_allowed or callback_token:
        try:
            hub_ports = HttpHubSpeechTrainingPorts(
                endpoint=callback_endpoint,
                allowed_endpoints=callback_allowed,
                bearer_token=callback_token,
            )
        except ValueError:
            return _UnavailableRuntime()
        authority = hub_ports
        dataset_resolver = HubValidatedMockDatasetResolver(root / "dataset-views", hub_ports)
        artifact_port = hub_ports
    else:
        # Explicit CI-only fallback retained for isolated contract tests.  The
        # compose profile configures callbacks and never selects this path.
        authority = _CiBindingAuthority()
        dataset_resolver = _CiDatasetResolver(root)
        artifact_port = _CiHubArtifactPort()
    publisher = SpeechResultPublisher(artifact_port, root=root)
    runner = SpeechTrainingRunner(
        registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
        authority=authority,
        dataset_resolver=dataset_resolver,
        result_publisher=publisher,
        workspace_root=root,
        model_root=root / "models",
    )
    return SpeechTrainingRuntime(runner, max_workers=1)


class _CiBindingAuthority:
    def verify(self, job: SpeechAdaptationJob, *, phase: str) -> tuple[bool, str | None]:
        del phase
        now = int(time.time() * 1000)
        return (
            now < min(job.deadline_at_ms, job.fencing.lease_expires_at_ms, job.consent.expires_at_ms),
            "speech_ci_binding_expired",
        )


class _CiDatasetResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def open_admitted(self, job: SpeechAdaptationJob) -> SpeechDatasetView:
        dataset_root = self._root / "synthetic-dataset"
        dataset_root.mkdir(parents=True, exist_ok=True)
        return SpeechDatasetView(
            root=dataset_root,
            dataset_digest=job.dataset.dataset_digest,
            split_digest=job.dataset.split_digest,
            train_sample_count=job.dataset.train_sample_count,
            validation_sample_count=job.dataset.validation_sample_count,
        )


class _CiHubArtifactPort:
    def publish(self, **values: Any) -> PublicationReceipt:
        stream = values.pop("stream")
        content = stream.read()
        if len(content) != values["size_bytes"]:
            raise SpeechTrainingRuntimeError("speech_ci_artifact_size_mismatch", "CI artifact size changed")
        return PublicationReceipt(
            artifact_id=values["target_id"],
            artifact_ref=values["target_ref"],
            sha256=values["sha256"],
            size_bytes=values["size_bytes"],
        )


def _submission(state: _RuntimeJob) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": state.job.job_id,
        "attempt_id": state.job.attempt.attempt_id,
        "status": state.status,
    }


def _json_body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise SpeechTrainingRuntimeError("speech_worker_json_invalid", "JSON object body is required", status_code=400)
    return payload


def _domain_error(exc: Exception):
    reason = str(getattr(exc, "reason_code", "speech_worker_error"))
    status = int(getattr(exc, "status_code", 422))
    return _error(reason, status, retryable=bool(getattr(exc, "retryable", False)))


def _error(reason_code: str, status_code: int, *, retryable: bool = False):
    return (
        jsonify(
            {
                "error": {
                    "code": reason_code,
                    "message": "speech training request was rejected",
                    "retryable": retryable,
                }
            }
        ),
        status_code,
    )


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=int(os.getenv("ANANTA_SPEECH_TRAINING_PORT", "8097")), threaded=True)
