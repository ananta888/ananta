from __future__ import annotations

import time

import pytest

from tests.speech_adaptation_support import (
    AlwaysActiveAuthority,
    MemoryArtifactPort,
    SyntheticDatasetResolver,
    speech_job_payload,
)
from worker.runtime.speech_training_app import (
    SpeechTrainingRuntime,
    SpeechTrainingRuntimeError,
    create_app,
)
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.backends import MockSpeechTrainingBackend
from worker.speech_training.result_publisher import SpeechResultPublisher
from worker.speech_training.runner import SpeechTrainingRunner

TOKEN = "speech-worker-test-token-00000001"


def _runtime(tmp_path, now_ms: int):
    runner = SpeechTrainingRunner(
        registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
        authority=AlwaysActiveAuthority(),
        dataset_resolver=SyntheticDatasetResolver(tmp_path / "dataset"),
        result_publisher=SpeechResultPublisher(MemoryArtifactPort(), root=tmp_path),
        workspace_root=tmp_path,
        model_root=tmp_path / "models",
        clock_ms=lambda: now_ms + 1,
    )
    return SpeechTrainingRuntime(runner)


def _headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Ananta-Contract-Version": "ananta.speech-adaptation.v1",
    }


def test_worker_auth_readiness_job_and_drain_lifecycle(tmp_path) -> None:
    now = int(time.time() * 1000)
    app = create_app(runtime=_runtime(tmp_path, now), auth_token=TOKEN)
    client = app.test_client()

    assert client.get("/health").status_code == 401
    assert client.get("/ready", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    response = client.post(
        "/internal/v1/speech-training/jobs",
        json=speech_job_payload(now_ms=now),
        headers=_headers(),
    )
    assert response.status_code == 202
    job_id = response.get_json()["job_id"]
    terminal = None
    for _ in range(100):
        terminal = client.get(f"/internal/v1/speech-training/jobs/{job_id}", headers=_headers()).get_json()
        if terminal["status"] in {"completed", "failed", "cancelled", "dataset_only"}:
            break
        time.sleep(0.01)
    assert terminal["status"] == "completed"
    assert "artifact" in terminal["result"]

    assert client.post(
        "/internal/v1/speech-training/drain",
        json={"drain": True},
        headers=_headers(),
    ).status_code == 202
    assert client.get("/ready", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 503


def test_worker_rejects_stale_contract_and_oversized_body(tmp_path) -> None:
    now = int(time.time() * 1000)
    app = create_app(runtime=_runtime(tmp_path, now), auth_token=TOKEN, max_request_bytes=1024)
    client = app.test_client()
    bad_headers = {**_headers(), "X-Ananta-Contract-Version": "ananta.lora-training.v1"}
    response = client.post(
        "/internal/v1/speech-training/jobs",
        json=speech_job_payload(now_ms=now),
        headers=bad_headers,
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "speech_contract_version_unsupported"

    response = client.post(
        "/internal/v1/speech-training/jobs",
        data=b"{" + b" " * 2048 + b"}",
        content_type="application/json",
        headers=_headers(),
    )
    assert response.status_code == 413


def test_external_cancel_kills_child_process_cleans_workspace_and_restart_forgets_attempt(tmp_path) -> None:
    now = int(time.time() * 1000)
    runtime = _runtime(tmp_path, now)
    app = create_app(runtime=runtime, auth_token=TOKEN)
    client = app.test_client()
    payload = speech_job_payload(
        now_ms=now,
        scenario="subprocess_cancel",
        job_id="speech-job-subprocess-cancel",
        artifact_id="speech-adapter-subprocess-cancel",
    )
    accepted = client.post(
        "/internal/v1/speech-training/jobs",
        json=payload,
        headers=_headers(),
    )
    assert accepted.status_code == 202
    job_id = payload["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = client.get(
            f"/internal/v1/speech-training/jobs/{job_id}", headers=_headers()
        ).get_json()
        if current["status"] == "running":
            break
        time.sleep(0.01)
    assert current["status"] == "running"

    cancelled = client.post(
        f"/internal/v1/speech-training/jobs/{job_id}/cancel",
        json={
            "attempt_id": payload["attempt"]["attempt_id"],
            "fencing_digest": payload["fencing"]["fencing_digest"],
            "reason_code": "speech_training_cancelled",
        },
        headers=_headers(),
    )
    assert cancelled.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        terminal = client.get(
            f"/internal/v1/speech-training/jobs/{job_id}", headers=_headers()
        ).get_json()
        if terminal["status"] in {"cancelled", "failed"}:
            break
        time.sleep(0.01)
    assert terminal["status"] == "cancelled"
    assert terminal["result"]["reason_code"] == "speech_training_cancelled"
    assert not (tmp_path / job_id).exists()

    restarted = _runtime(tmp_path, now)
    assert restarted.readiness()["ready"] is True
    with pytest.raises(SpeechTrainingRuntimeError, match="speech job was not found"):
        restarted.status(job_id)
