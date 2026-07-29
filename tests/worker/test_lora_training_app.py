from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from ananta_contracts.unsloth_capability import compose_worker_capability_probe
from worker.runtime.lora_training_app import (
    CAPABILITIES_ENDPOINT,
    CLEANUP_ENDPOINT,
    EVALUATIONS_ENDPOINT,
    JOBS_ENDPOINT,
    create_app,
)
from worker.training.contracts import CONTRACT_VERSION
from worker.training.runtime import TrainingRuntimeError
from worker.training.storage_cleanup import WorkerStorageCleanupError

TEST_TOKEN = "lora-training-test-token-123456"


class _Runtime:
    def __init__(self, artifact: Path | None = None) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.artifact_path = artifact

    def health(self) -> dict[str, Any]:
        return {"contract_version": CONTRACT_VERSION, "status": "ready", "runtime_configured": True}

    def capability_probe(self) -> dict[str, Any]:
        return compose_worker_capability_probe(
            contract_version=CONTRACT_VERSION,
            resource_profile="nvidia",
            active_gpu_profile="rtx3080-safe",
            backend_availability={
                backend: (True, None)
                for backend in (
                    "mock",
                    "peft_trl",
                    "unsloth",
                    "unsloth_vision",
                    "unsloth_audio",
                    "unsloth_embedding",
                )
            },
            package_versions={"torch": "2.7.0", "unsloth": "2026.7", "unsloth_zoo": "2026.7"},
            hardware={
                "cuda_available": True,
                "torch_version": "2.7.0",
                "cuda_version": "12.8",
                "device_count": 1,
                "device_name": "RTX 3080",
                "total_vram_bytes": 10 * 1024**3,
            },
            runtime_ready=True,
        )

    def submit(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(envelope)
        return self._status(str(envelope.get("job_id") or "job-1"))

    def cleanup(
        self,
        envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(envelope)
        return {
            "schema": "ananta.unsloth-storage-cleanup-result.v1",
            "task_id": str(envelope.get("task_id") or "cleanup-1"),
            "tenant_scope_digest": "a" * 64,
            "plan_sha256": "b" * 64,
            "status": "completed",
            "deleted_count": 0,
            "artifacts": [],
            "paths_exposed": False,
            "replayed": False,
        }

    def status(self, job_id: str) -> dict[str, Any]:
        if job_id == "missing":
            raise TrainingRuntimeError("job_not_found", "training job does not exist", http_status=404)
        return self._status(job_id)

    def heartbeat(self, job_id: str) -> dict[str, Any]:
        return self.status(job_id)

    def events(self, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        return {"contract_version": CONTRACT_VERSION, "job_id": job_id, "events": [], "next_sequence": after_sequence}

    def cancel(self, job_id: str) -> dict[str, Any]:
        return {**self._status(job_id), "status": "cancel_requested"}

    def artifact(self, job_id: str, artifact_name: str):
        if self.artifact_path is None:
            raise TrainingRuntimeError("artifact_not_found", "artifact does not exist", http_status=404)
        return self.artifact_path, {
            "name": artifact_name,
            "sha256": "a" * 64,
            "media_type": "application/octet-stream",
        }

    @staticmethod
    def _status(job_id: str) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": job_id,
            "attempt_id": "attempt-1",
            "fencing_token": 1,
            "correlation_id": "correlation-1",
            "status": "queued",
        }


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def test_health_is_lightweight_but_reports_auth_readiness() -> None:
    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()

    response = client.get("/health", headers=_headers())

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert response.get_json()["auth_configured"] is True
    assert runtime.calls == []


def test_weak_or_missing_server_token_fails_closed() -> None:
    client = create_app(runtime=_Runtime(), auth_token="too-short").test_client()

    health = client.get("/health", headers={"Authorization": "Bearer too-short"})
    submit = client.post(JOBS_ENDPOINT, json={}, headers={"Authorization": "Bearer too-short"})

    assert health.status_code == 503
    assert health.get_json()["error"]["code"] == "auth_not_configured"
    assert submit.status_code == 503
    assert submit.get_json()["error"]["code"] == "auth_not_configured"


def test_every_internal_endpoint_requires_bearer_authentication() -> None:
    client = create_app(runtime=_Runtime(), auth_token=TEST_TOKEN).test_client()
    requests = [
        client.get("/health"),
        client.get(CAPABILITIES_ENDPOINT),
        client.post(JOBS_ENDPOINT, json={}),
        client.post(EVALUATIONS_ENDPOINT, json={}),
        client.post(CLEANUP_ENDPOINT, json={}),
        client.get(f"{JOBS_ENDPOINT}/job-1"),
        client.post(f"{JOBS_ENDPOINT}/job-1/heartbeat"),
        client.get(f"{JOBS_ENDPOINT}/job-1/events"),
        client.post(f"{JOBS_ENDPOINT}/job-1/cancel"),
        client.get(f"{JOBS_ENDPOINT}/job-1/artifacts/adapter.safetensors"),
    ]

    assert all(response.status_code == 401 for response in requests)
    assert all(response.headers["WWW-Authenticate"] == "Bearer" for response in requests)


def test_capability_probe_is_authenticated_and_worker_owned() -> None:
    client = create_app(runtime=_Runtime(), auth_token=TEST_TOKEN).test_client()

    response = client.get(CAPABILITIES_ENDPOINT, headers=_headers())

    assert response.status_code == 200
    assert response.get_json()["schema_version"] == "ananta.unsloth-worker-capabilities.v1"
    assert response.get_json()["hardware"]["cuda_available"] is True
    assert response.get_json()["compositions"]["studio"]["available"] is False


def test_submit_is_async_and_preserves_correlation_contract() -> None:
    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()
    envelope = {"job_id": "job-42", "correlation_id": "correlation-1"}

    response = client.post(JOBS_ENDPOINT, json=envelope, headers=_headers())

    assert response.status_code == 202
    assert response.get_json()["job_id"] == "job-42"
    assert response.get_json()["correlation_id"] == "correlation-1"
    assert runtime.calls == [envelope]


def test_adapter_evaluation_has_dedicated_async_endpoint() -> None:
    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()
    envelope = {
        "job_id": "evaluation-1",
        "correlation_id": "correlation-1",
        "job_type": "evaluate_existing_adapter",
    }

    response = client.post(EVALUATIONS_ENDPOINT, json=envelope, headers=_headers())
    wrong_type = client.post(EVALUATIONS_ENDPOINT, json={**envelope, "job_type": "train_lora"}, headers=_headers())

    assert response.status_code == 202
    assert response.get_json()["job_id"] == "evaluation-1"
    assert wrong_type.status_code == 422


def test_cleanup_preserves_the_worker_reason_code() -> None:
    class RejectingRuntime(_Runtime):
        def cleanup(
            self,
            envelope: Mapping[str, Any],
        ) -> dict[str, Any]:
            raise WorkerStorageCleanupError(
                "cleanup_artifact_hash_mismatch",
                "bounded cleanup rejection",
            )

    client = create_app(
        runtime=RejectingRuntime(),
        auth_token=TEST_TOKEN,
    ).test_client()
    response = client.post(
        CLEANUP_ENDPOINT,
        json={"task_id": "cleanup-1"},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == (
        "cleanup_artifact_hash_mismatch"
    )


def test_request_size_media_type_pagination_and_not_found_are_bounded() -> None:
    client = create_app(runtime=_Runtime(), auth_token=TEST_TOKEN, max_request_bytes=1024).test_client()
    non_json = client.post(JOBS_ENDPOINT, data="plain", headers=_headers())
    oversized = client.post(
        JOBS_ENDPOINT,
        data=b"{" + b"x" * 2048 + b"}",
        headers={**_headers(), "Content-Type": "application/json"},
    )
    invalid_cursor = client.get(f"{JOBS_ENDPOINT}/job-1/events?after_sequence=nope", headers=_headers())
    missing = client.get(f"{JOBS_ENDPOINT}/missing", headers=_headers())

    assert non_json.status_code == 415
    assert oversized.status_code == 413
    assert oversized.get_json()["error"]["code"] == "request_too_large"
    assert invalid_cursor.status_code == 422
    assert missing.status_code == 404


def test_artifact_response_is_attachment_with_integrity_header(tmp_path: Path) -> None:
    artifact = tmp_path / "adapter.safetensors"
    artifact.write_bytes(b"adapter")
    client = create_app(runtime=_Runtime(artifact), auth_token=TEST_TOKEN).test_client()

    response = client.get(f"{JOBS_ENDPOINT}/job-1/artifacts/adapter.safetensors", headers=_headers())

    assert response.status_code == 200
    assert response.data == b"adapter"
    assert response.headers["X-Artifact-SHA256"] == "a" * 64
    assert "attachment" in response.headers["Content-Disposition"]


def test_control_plane_import_does_not_load_ml_frameworks_or_hub_modules() -> None:
    script = (
        "import sys; import worker.runtime.lora_training_app; "
        "forbidden={'torch','transformers','peft','trl','unsloth'}; "
        "assert not forbidden.intersection(sys.modules); "
        "assert not any(name == 'agent' or name.startswith('agent.') for name in sys.modules)"
    )

    result = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
