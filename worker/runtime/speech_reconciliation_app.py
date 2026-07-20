"""Authenticated HTTP boundary for the isolated reconciliation worker."""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationContractError,
)
from voice_runtime.backends.registry import build_default_voice_backend_registry
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.model_manifest import load_catalog_for_config
from worker.speech_reconciliation.asr_ensemble import (
    LocalSpeechModel,
    LocalSpeechModelRegistry,
    SpeechAsrEnsemble,
    SpeechAsrEnsembleError,
)
from worker.speech_reconciliation.audio_staging import (
    AesGcmSpeechArtifactDecryptor,
    EpochKeyring,
    SpeechAudioStager,
    SpeechAudioStagingError,
    SpeechStageAuthority,
)
from worker.speech_reconciliation.checkpointing import (
    AesGcmSpeechCheckpointCipher,
    SpeechCheckpointError,
    SpeechReconciliationCheckpointStore,
)
from worker.speech_reconciliation.contracts import (
    MAX_AUDIO_CIPHERTEXT_BYTES,
    SpeechReconciliationWorkerOutcome,
    SpeechReconciliationWorkerTask,
    assert_worker_outcome_matches_job,
)
from worker.speech_reconciliation.resolver import (
    SpeechReconciliationResolutionError,
    SpeechReconciliationResolver,
)
from worker.speech_reconciliation.runner import SpeechReconciliationRunner

BASE_PATH = "/internal/v1/speech-reconciliation"
MIN_TOKEN_LENGTH = 24
MAX_TASK_JSON_BYTES = 1024 * 1024
DEFAULT_MAX_REQUEST_BYTES = MAX_AUDIO_CIPHERTEXT_BYTES
_FINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})


class SpeechReconciliationRuntimeError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(reason_code)


class SpeechReconciliationExecutionPort(Protocol):
    def run(
        self,
        task: SpeechReconciliationWorkerTask,
        ciphertext: bytes,
        *,
        cancellation_check,
    ) -> Mapping[str, object]: ...


class _CancellationSignal:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self._event.is_set():
            raise SpeechReconciliationRuntimeError("speech_reconciliation_cancelled")


@dataclass
class _RuntimeJob:
    task: SpeechReconciliationWorkerTask
    cancellation: _CancellationSignal
    status: str = "awaiting_audio"
    result: dict[str, object] | None = None


class SpeechReconciliationRuntime:
    """Bounded execution registry; scheduling/leases remain Hub-owned."""

    def __init__(
        self,
        executor: SpeechReconciliationExecutionPort,
        *,
        max_workers: int = 1,
        maximum_history: int = 128,
    ) -> None:
        if not 1 <= max_workers <= 4 or not 1 <= maximum_history <= 1024:
            raise ValueError("speech reconciliation runtime limits are invalid")
        self._executor = executor
        self._max_workers = max_workers
        self._maximum_history = maximum_history
        self._jobs: dict[str, _RuntimeJob] = {}
        self._lock = threading.RLock()
        self._draining = False

    def health(self) -> Mapping[str, object]:
        with self._lock:
            return {
                "contract_version": CONTRACT_VERSION,
                "status": "ok",
                "active_jobs": self._active_count(),
                "draining": self._draining,
            }

    def readiness(self) -> Mapping[str, object]:
        with self._lock:
            ready = not self._draining and self._active_count() < self._max_workers
            return {
                "contract_version": CONTRACT_VERSION,
                "ready": ready,
                "reason_code": None
                if ready
                else "speech_reconciliation_worker_draining"
                if self._draining
                else "speech_reconciliation_worker_busy",
            }

    def submit(self, task: SpeechReconciliationWorkerTask) -> Mapping[str, object]:
        with self._lock:
            existing = self._jobs.get(task.job.job_id)
            if existing is not None:
                if not secrets.compare_digest(existing.task.binding_digest, task.binding_digest):
                    raise SpeechReconciliationRuntimeError(
                        "speech_reconciliation_job_binding_conflict",
                        status_code=409,
                    )
                return _submission(existing)
            if self._draining:
                raise SpeechReconciliationRuntimeError(
                    "speech_reconciliation_worker_draining",
                    status_code=503,
                    retryable=True,
                )
            if self._active_count() >= self._max_workers:
                raise SpeechReconciliationRuntimeError(
                    "speech_reconciliation_worker_capacity_exhausted",
                    status_code=503,
                    retryable=True,
                )
            self._evict_history()
            state = _RuntimeJob(task=task, cancellation=_CancellationSignal())
            self._jobs[task.job.job_id] = state
            return _submission(state)

    def upload_audio(self, job_id: str, payload: bytes) -> Mapping[str, object]:
        with self._lock:
            state = self._require(job_id)
            if state.status != "awaiting_audio":
                if state.status in {"accepted", "running", *_FINAL_STATUSES}:
                    return _submission(state)
                raise SpeechReconciliationRuntimeError("speech_reconciliation_audio_state_conflict", status_code=409)
            if len(payload) != state.task.audio_artifact.ciphertext_bytes:
                raise SpeechReconciliationRuntimeError("speech_reconciliation_artifact_size_mismatch")
            state.status = "accepted"
            thread = threading.Thread(
                target=self._execute,
                args=(state, bytes(payload)),
                name=f"speech-reconciliation-{state.task.job.job_id[:32]}",
                daemon=True,
            )
            thread.start()
            return _submission(state)

    def status(self, job_id: str) -> Mapping[str, object]:
        with self._lock:
            state = self._require(job_id)
            return {
                "contract_version": CONTRACT_VERSION,
                "job_id": state.task.job.job_id,
                "attempt_id": state.task.job.attempt_id,
                "fencing_epoch": state.task.job.fencing_epoch,
                "status": state.status,
                "result": dict(state.result) if state.result is not None else None,
            }

    def cancel(
        self,
        job_id: str,
        *,
        attempt_id: str,
        fencing_token_digest: str,
    ) -> Mapping[str, object]:
        with self._lock:
            state = self._require(job_id)
            if state.task.job.attempt_id != attempt_id or not secrets.compare_digest(
                state.task.job.fencing_token_digest, fencing_token_digest
            ):
                raise SpeechReconciliationRuntimeError(
                    "speech_reconciliation_cancel_fencing_mismatch",
                    status_code=409,
                )
            if state.status in _FINAL_STATUSES:
                return {"job_id": job_id, "status": state.status}
            state.cancellation.cancel()
            if state.status == "awaiting_audio":
                state.status = "cancelled"
                state.result = SpeechReconciliationWorkerOutcome.failure(
                    state.task.job,
                    status="cancelled",
                    reason_code="speech_reconciliation_cancelled",
                ).to_dict()
            else:
                state.status = "cancel_requested"
            return {"job_id": job_id, "status": state.status}

    def drain(self) -> Mapping[str, object]:
        with self._lock:
            self._draining = True
        return {"status": "draining"}

    def _execute(self, state: _RuntimeJob, ciphertext: bytes) -> None:
        with self._lock:
            if state.status == "accepted":
                state.status = "running"
        try:
            outcome = SpeechReconciliationWorkerOutcome.from_mapping(
                self._executor.run(
                    state.task,
                    ciphertext,
                    cancellation_check=state.cancellation.check,
                )
            )
            assert_worker_outcome_matches_job(state.task.job, outcome)
            status = outcome.status
            result = outcome.to_dict()
        except (
            SpeechReconciliationContractError,
            SpeechAudioStagingError,
            SpeechAsrEnsembleError,
            SpeechCheckpointError,
            SpeechReconciliationResolutionError,
            SpeechReconciliationRuntimeError,
        ) as exc:
            status = "cancelled" if exc.reason_code == "speech_reconciliation_cancelled" else "failed"
            result = SpeechReconciliationWorkerOutcome.failure(
                state.task.job,
                status=status,
                reason_code=exc.reason_code,
                retryable=bool(getattr(exc, "retryable", False)),
            ).to_dict()
        except Exception:
            status = "failed"
            result = SpeechReconciliationWorkerOutcome.failure(
                state.task.job,
                status="failed",
                reason_code="speech_reconciliation_internal_failure",
            ).to_dict()
        finally:
            # No ciphertext is retained on the runtime object or result.
            del ciphertext
        with self._lock:
            if state.status == "cancel_requested":
                status = "cancelled"
                result = SpeechReconciliationWorkerOutcome.failure(
                    state.task.job,
                    status="cancelled",
                    reason_code="speech_reconciliation_cancelled",
                ).to_dict()
            state.result = result
            state.status = status

    def _require(self, job_id: str) -> _RuntimeJob:
        state = self._jobs.get(job_id)
        if state is None:
            raise SpeechReconciliationRuntimeError("speech_reconciliation_job_not_found", status_code=404)
        return state

    def _active_count(self) -> int:
        return sum(state.status not in _FINAL_STATUSES for state in self._jobs.values())

    def _evict_history(self) -> None:
        while len(self._jobs) >= self._maximum_history:
            final = next((job_id for job_id, item in self._jobs.items() if item.status in _FINAL_STATUSES), None)
            if final is None:
                raise SpeechReconciliationRuntimeError(
                    "speech_reconciliation_worker_history_full",
                    status_code=503,
                    retryable=True,
                )
            self._jobs.pop(final, None)


class _AdmittedStageAuthority(SpeechStageAuthority):
    def verify(self, task: SpeechReconciliationWorkerTask, *, phase: str) -> tuple[bool, str | None]:
        del phase
        if time.time_ns() // 1_000_000 >= task.job.deadline_at_ms:
            return False, "speech_reconciliation_deadline_expired"
        return True, None


class _UnavailableRuntime:
    def health(self) -> Mapping[str, object]:
        return {"contract_version": CONTRACT_VERSION, "status": "degraded", "active_jobs": 0, "draining": False}

    def readiness(self) -> Mapping[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "ready": False,
            "reason_code": "speech_reconciliation_worker_not_configured",
        }

    def _raise(self):
        raise SpeechReconciliationRuntimeError(
            "speech_reconciliation_worker_not_configured",
            status_code=503,
            retryable=True,
        )

    def submit(self, task: SpeechReconciliationWorkerTask):
        del task
        self._raise()

    def upload_audio(self, job_id: str, payload: bytes):
        del job_id, payload
        self._raise()

    def status(self, job_id: str):
        del job_id
        self._raise()

    def cancel(self, job_id: str, *, attempt_id: str, fencing_token_digest: str):
        del job_id, attempt_id, fencing_token_digest
        self._raise()

    def drain(self) -> Mapping[str, object]:
        return {"status": "draining"}


def create_app(
    *,
    runtime=None,
    auth_token: str | None = None,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_request_bytes
    worker = runtime or _runtime_from_environment()
    token = str(auth_token if auth_token is not None else os.getenv("ANANTA_SPEECH_RECONCILIATION_TOKEN", "")).strip()
    token_ready = len(token) >= MIN_TOKEN_LENGTH and not any(character.isspace() for character in token)

    @app.before_request
    def authorize():
        if not token_ready:
            return _error("speech_reconciliation_worker_auth_not_configured", 503, retryable=True)
        expected = f"Bearer {token}"
        if not secrets.compare_digest(str(request.headers.get("Authorization") or ""), expected):
            return _error("speech_reconciliation_worker_unauthorized", 401)
        if request.path.startswith(BASE_PATH):
            version = str(request.headers.get("X-Ananta-Contract-Version") or "")
            if version != CONTRACT_VERSION:
                return _error("speech_reconciliation_contract_version_invalid", 422)
        return None

    @app.get("/health")
    def health():
        payload = dict(worker.health())
        return jsonify(payload), 200

    @app.get("/ready")
    def ready():
        payload = dict(worker.readiness())
        return jsonify(payload), 200 if payload.get("ready") else 503

    @app.post(f"{BASE_PATH}/jobs")
    def submit():
        try:
            if int(request.content_length or 0) > MAX_TASK_JSON_BYTES:
                raise SpeechReconciliationRuntimeError("speech_reconciliation_task_size_limit", status_code=413)
            task = SpeechReconciliationWorkerTask.from_mapping(_json_body())
            return jsonify(dict(worker.submit(task))), 202
        except (SpeechReconciliationContractError, SpeechReconciliationRuntimeError) as exc:
            return _domain_error(exc)

    @app.put(f"{BASE_PATH}/jobs/<job_id>/audio")
    def upload_audio(job_id: str):
        try:
            if request.mimetype != "application/octet-stream":
                raise SpeechReconciliationRuntimeError("speech_reconciliation_artifact_content_type_invalid")
            payload = request.get_data(cache=False, as_text=False)
            return jsonify(dict(worker.upload_audio(job_id, payload))), 202
        except SpeechReconciliationRuntimeError as exc:
            return _domain_error(exc)

    @app.get(f"{BASE_PATH}/jobs/<job_id>")
    def status(job_id: str):
        try:
            return jsonify(dict(worker.status(job_id))), 200
        except SpeechReconciliationRuntimeError as exc:
            return _domain_error(exc)

    @app.post(f"{BASE_PATH}/jobs/<job_id>/cancel")
    def cancel(job_id: str):
        try:
            payload = _json_body()
            if set(payload) != {"attempt_id", "fencing_token_digest"}:
                raise SpeechReconciliationRuntimeError("speech_reconciliation_cancel_shape_invalid")
            return (
                jsonify(
                    dict(
                        worker.cancel(
                            job_id,
                            attempt_id=str(payload["attempt_id"]),
                            fencing_token_digest=str(payload["fencing_token_digest"]),
                        )
                    )
                ),
                202,
            )
        except SpeechReconciliationRuntimeError as exc:
            return _domain_error(exc)

    @app.post(f"{BASE_PATH}/drain")
    def drain():
        return jsonify(dict(worker.drain())), 202

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return _error("speech_reconciliation_request_too_large", 413)

    return app


def _runtime_from_environment():
    try:
        keyring_path = Path(os.environ["ANANTA_SPEECH_RECONCILIATION_KEYRING_PATH"])
        workspace_root = Path(os.getenv("ANANTA_SPEECH_RECONCILIATION_WORKSPACE_ROOT", "/work/audio"))
        checkpoint_root = Path(os.getenv("ANANTA_SPEECH_RECONCILIATION_CHECKPOINT_ROOT", "/work/checkpoints"))
        allowed = tuple(
            item.strip()
            for item in os.getenv("ANANTA_SPEECH_RECONCILIATION_ALLOWED_MODELS", "").split(",")
            if item.strip()
        )
        if not allowed or len(allowed) > 16 or "mock" in allowed:
            return _UnavailableRuntime()
        config = VoiceRuntimeConfig.from_env()
        config.validate()
        catalog = load_catalog_for_config(config)
        if catalog is None:
            return _UnavailableRuntime()
        factories = build_default_voice_backend_registry()
        models: dict[str, LocalSpeechModel] = {}
        for model_id in allowed:
            manifest = catalog.require_model(model_id)
            backend_id = manifest.engine
            models[model_id] = LocalSpeechModel(
                model_id=model_id,
                model_revision=manifest.revision,
                manifest_digest=manifest.manifest_digest.removeprefix("sha256:"),
                backend=factories.create_lazy(backend_id, config=config, model_catalog=catalog),
                device="gpu" if config.device in {"cuda", "gpu"} else "cpu",
                ram_bytes=manifest.ram_bytes,
                vram_bytes=manifest.vram_bytes,
                concurrency_slots=manifest.concurrency_slots,
            )
        keys = EpochKeyring.from_file(keyring_path)
        stager = SpeechAudioStager(
            workspace_root=workspace_root,
            decryptor=AesGcmSpeechArtifactDecryptor(keys),
            authority=_AdmittedStageAuthority(),
        )
        checkpoints = SpeechReconciliationCheckpointStore(
            checkpoint_root,
            cipher=AesGcmSpeechCheckpointCipher(keys),
        )
        runner = SpeechReconciliationRunner(
            stager=stager,
            ensemble=SpeechAsrEnsemble(models=LocalSpeechModelRegistry(models)),
            resolver=SpeechReconciliationResolver(),
            checkpoints=checkpoints,
        )
        return SpeechReconciliationRuntime(
            runner,
            max_workers=max(1, min(4, int(os.getenv("ANANTA_SPEECH_RECONCILIATION_MAX_WORKERS", "1")))),
        )
    except (KeyError, OSError, ValueError, SpeechAudioStagingError, SpeechCheckpointError):
        return _UnavailableRuntime()


def _submission(state: _RuntimeJob) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": state.task.job.job_id,
        "attempt_id": state.task.job.attempt_id,
        "fencing_epoch": state.task.job.fencing_epoch,
        "status": state.status,
    }


def _json_body() -> dict[str, object]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise SpeechReconciliationRuntimeError("speech_reconciliation_json_invalid")
    return payload


def _domain_error(exc):
    if isinstance(exc, SpeechReconciliationContractError):
        return _error(exc.reason_code, 422)
    return _error(exc.reason_code, exc.status_code, retryable=exc.retryable)


def _error(reason_code: str, status_code: int, *, retryable: bool = False):
    return jsonify({"error": {"reason_code": reason_code, "retryable": retryable}}), status_code


def main() -> None:
    app = create_app()
    port = max(1, min(65_535, int(os.getenv("ANANTA_SPEECH_RECONCILIATION_PORT", "8098"))))
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()


__all__ = [
    "BASE_PATH",
    "SpeechReconciliationRuntime",
    "SpeechReconciliationRuntimeError",
    "create_app",
    "main",
]
