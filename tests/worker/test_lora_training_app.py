from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from worker.runtime.lora_training_app import EVALUATIONS_ENDPOINT, JOBS_ENDPOINT, create_app
from worker.training.contracts import CONTRACT_VERSION
from worker.training.runtime import TrainingRuntimeError

TEST_TOKEN = "lora-training-test-token-123456"


class _Runtime:
    def __init__(self, artifact: Path | None = None) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.artifact_path = artifact

    def health(self) -> dict[str, Any]:
        return {"contract_version": CONTRACT_VERSION, "status": "ready", "runtime_configured": True}

    def submit(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(envelope)
        return self._status(str(envelope.get("job_id") or "job-1"))

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
        client.post(JOBS_ENDPOINT, json={}),
        client.post(EVALUATIONS_ENDPOINT, json={}),
        client.get(f"{JOBS_ENDPOINT}/job-1"),
        client.post(f"{JOBS_ENDPOINT}/job-1/heartbeat"),
        client.get(f"{JOBS_ENDPOINT}/job-1/events"),
        client.post(f"{JOBS_ENDPOINT}/job-1/cancel"),
        client.get(f"{JOBS_ENDPOINT}/job-1/artifacts/adapter.safetensors"),
    ]

    assert all(response.status_code == 401 for response in requests)
    assert all(response.headers["WWW-Authenticate"] == "Bearer" for response in requests)


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
