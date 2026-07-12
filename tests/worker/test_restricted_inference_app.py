from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from agent.services.restricted_inference_contract import CONTRACT_VERSION
from worker.runtime.restricted_inference_app import INFERENCE_ENDPOINT, _runtime_from_environment, create_app

TEST_TOKEN = "restricted-inference-test-token"


class _Runtime:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.response = response or {
            "contract_version": CONTRACT_VERSION,
            "request_id": "request-1",
            "task_id": "task-1",
            "operation": "classify",
            "status": "succeeded",
            "result": {
                "label": "safe",
                "confidence": 0.9,
                "all_scores": {"safe": 0.9},
                "engine": "huggingface-transformers",
                "model_id": "org/model",
                "manifest_digest": "a" * 64,
                "latency_ms": 1.0,
            },
            "error": None,
            "no_generation": True,
        }

    def handle(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(envelope)
        return self.response


def _envelope() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "request-1",
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "operation": "classify",
        "payload": {"text": "question", "labels": ["safe", "unsafe"]},
        "model_manifest_id": "manifest-1",
        "policy_hash": "policy-1",
        "deadline_epoch_ms": 2_000_000_000_000,
        "paths": [],
        "idempotency_key": "",
    }


def test_health_is_unauthenticated_lightweight_and_does_not_call_runtime() -> None:
    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert response.get_json()["contract_version"] == CONTRACT_VERSION
    assert runtime.calls == []


def test_inference_endpoint_requires_bearer_token() -> None:
    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()

    missing = client.post(INFERENCE_ENDPOINT, json=_envelope())
    invalid = client.post(INFERENCE_ENDPOINT, json=_envelope(), headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert runtime.calls == []


def test_inference_endpoint_dispatches_authenticated_contract_envelope() -> None:
    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()

    response = client.post(
        INFERENCE_ENDPOINT,
        json=_envelope(),
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.get_json()["no_generation"] is True
    assert len(runtime.calls) == 1


def test_missing_server_token_fails_closed() -> None:
    client = create_app(runtime=_Runtime(), auth_token="").test_client()

    health = client.get("/health")
    response = client.post(INFERENCE_ENDPOINT, json=_envelope(), headers={"Authorization": "Bearer anything"})

    assert health.status_code == 200
    assert health.get_json()["status"] == "degraded"
    assert health.get_json()["auth_configured"] is False
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "auth_not_configured"


def test_weak_server_token_fails_closed() -> None:
    client = create_app(runtime=_Runtime(), auth_token="too-short").test_client()

    health = client.get("/health")
    response = client.post(
        INFERENCE_ENDPOINT,
        json=_envelope(),
        headers={"Authorization": "Bearer too-short"},
    )

    assert health.get_json()["status"] == "degraded"
    assert health.get_json()["auth_configured"] is False
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "auth_not_configured"


def test_inference_endpoint_rejects_non_json_and_oversized_body() -> None:
    client = create_app(runtime=_Runtime(), auth_token=TEST_TOKEN, max_request_bytes=1024).test_client()
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}

    non_json = client.post(INFERENCE_ENDPOINT, data="plain", headers=headers)
    oversized = client.post(
        INFERENCE_ENDPOINT,
        data=b"{" + b"x" * 2048 + b"}",
        headers={**headers, "Content-Type": "application/json"},
    )

    assert non_json.status_code == 415
    assert oversized.status_code == 413
    assert oversized.get_json()["error"]["code"] == "request_too_large"


def test_default_runtime_is_fail_closed_but_service_remains_healthy(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", TEST_TOKEN)
    client = create_app().test_client()

    health = client.get("/health")
    response = client.post(
        INFERENCE_ENDPOINT,
        json=_envelope(),
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )

    assert health.status_code == 200
    assert health.get_json()["runtime_configured"] is False
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "manifest_unavailable"


def test_environment_runtime_passes_exact_engine_and_device_policy_to_executor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest_root = tmp_path / "manifests"
    snapshot_root = tmp_path / "snapshots"
    manifest_root.mkdir()
    snapshot_root.mkdir()
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT", str(manifest_root))
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT", str(snapshot_root))
    monkeypatch.setenv(
        "RESTRICTED_INFERENCE_ENABLED_ENGINES",
        "sentence-transformers,huggingface-transformers",
    )
    monkeypatch.setenv("RESTRICTED_INFERENCE_DEVICE", "cuda")
    captured: dict[str, Any] = {}

    def _build_default_executor(**kwargs):
        captured.update(kwargs)
        return object(), object()

    monkeypatch.setattr(
        "worker.runtime.restricted_inference_executor.build_default_executor",
        _build_default_executor,
    )

    runtime = _runtime_from_environment()

    assert runtime is not None
    assert captured["enabled_engines"] == frozenset(
        {"sentence-transformers", "huggingface-transformers"}
    )
    assert captured["worker_device"] == "cuda"


def test_app_import_does_not_import_optional_ml_libraries() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    script = """
import sys
import worker.runtime.restricted_inference_app
for name in ('torch', 'transformers', 'sentence_transformers', 'onnxruntime'):
    assert name not in sys.modules, name
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
