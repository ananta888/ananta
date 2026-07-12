from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from agent.services.restricted_inference_contract import (
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    RestrictedInferenceStatus,
)
from agent.services.restricted_inference_model_manifest import (
    ModelManifestValidationError,
    VerifiedModelSnapshot,
)
from worker.runtime.restricted_inference_runtime import RestrictedInferenceWorkerRuntime


def _request(*, deadline_epoch_ms: int = 2000) -> RestrictedInferenceRequest:
    return RestrictedInferenceRequest(
        request_id="request-1",
        task_id="task-1",
        tenant_id="tenant-1",
        operation=RestrictedInferenceOperation.CLASSIFY,
        payload={"text": "security issue", "labels": ["safe", "unsafe"]},
        model_manifest_id="manifest-1",
        policy_hash="policy-1",
        deadline_epoch_ms=deadline_epoch_ms,
    )


def _snapshot() -> VerifiedModelSnapshot:
    return VerifiedModelSnapshot(
        root=Path("/verified/model"),
        manifest_id="manifest-1",
        manifest_digest="a" * 64,
        model_id="org/model",
        engine="huggingface-transformers",
        total_size_bytes=1,
        file_digests={"model.safetensors": "b" * 64},
    )


class _Admission:
    def __init__(self, snapshot: VerifiedModelSnapshot | None = None) -> None:
        self.snapshot = snapshot or _snapshot()
        self.calls: list[str] = []

    def admit(self, manifest_id: str) -> VerifiedModelSnapshot:
        self.calls.append(manifest_id)
        return self.snapshot


class _Executor:
    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        self.result = result or {"label": "unsafe", "confidence": 0.9, "all_scores": {"unsafe": 0.9}}
        self.calls = 0

    def execute(
        self,
        request: RestrictedInferenceRequest,
        snapshot: VerifiedModelSnapshot,
    ) -> Mapping[str, Any]:
        self.calls += 1
        return self.result


def test_runtime_admits_snapshot_binds_provenance_and_returns_contract_response() -> None:
    admission = _Admission()
    executor = _Executor()
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=admission,
        executor=executor,
        epoch_ms=lambda: 1000,
        monotonic_ns=iter((1_000_000, 3_000_000)).__next__,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(_request().to_dict()))

    assert response.status is RestrictedInferenceStatus.SUCCEEDED
    assert response.result is not None
    assert response.result["manifest_digest"] == "a" * 64
    assert response.result["model_id"] == "org/model"
    assert response.result["latency_ms"] == 2.0
    assert admission.calls == ["manifest-1"]
    assert executor.calls == 1


def test_expired_request_never_admits_or_executes_model() -> None:
    admission = _Admission()
    executor = _Executor()
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=admission,
        executor=executor,
        epoch_ms=lambda: 2000,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(_request(deadline_epoch_ms=2000).to_dict()))

    assert response.status is RestrictedInferenceStatus.FAILED
    assert response.error is not None and response.error.code == "timeout"
    assert response.error.retryable is True
    assert admission.calls == []
    assert executor.calls == 0


class _RejectedAdmission(_Admission):
    def admit(self, manifest_id: str) -> VerifiedModelSnapshot:
        raise ModelManifestValidationError("hash_mismatch", "do not expose snapshot path")


def test_snapshot_admission_failure_is_redacted_and_skips_executor() -> None:
    executor = _Executor()
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=_RejectedAdmission(),
        executor=executor,
        epoch_ms=lambda: 1000,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(_request().to_dict()))

    assert response.error is not None and response.error.code == "hash_mismatch"
    assert "path" not in response.error.message
    assert executor.calls == 0


def test_runtime_rejects_generated_result_field() -> None:
    executor = _Executor(
        {
            "label": "unsafe",
            "confidence": 0.9,
            "all_scores": {"unsafe": 0.9},
            "generated_text": "invented explanation",
        }
    )
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=_Admission(),
        executor=executor,
        epoch_ms=lambda: 1000,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(_request().to_dict()))

    assert response.status is RestrictedInferenceStatus.FAILED
    assert response.error is not None
    assert response.error.code == "invalid_result_shape"
    assert response.result is None


def test_runtime_rejects_executor_provenance_spoofing() -> None:
    executor = _Executor(
        {
            "label": "unsafe",
            "confidence": 0.9,
            "all_scores": {"unsafe": 0.9},
            "model_id": "attacker/model",
        }
    )
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=_Admission(),
        executor=executor,
        epoch_ms=lambda: 1000,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(_request().to_dict()))

    assert response.error is not None
    assert response.error.code == "result_provenance_mismatch"


def test_production_runtime_requires_run_correlation_before_admission() -> None:
    admission = _Admission()
    executor = _Executor()
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=admission,
        executor=executor,
        epoch_ms=lambda: 1000,
        require_run_id=True,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(_request().to_dict()))

    assert response.error is not None
    assert response.error.code == "run_id_required"
    assert admission.calls == []
    assert executor.calls == 0
