from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent.services.restricted_inference_contract import (
    CONTRACT_VERSION,
    RestrictedInferenceContractError,
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    RestrictedInferenceStatus,
)
from agent.services.restricted_inference_port import ContractRestrictedInferencePort


def _request() -> RestrictedInferenceRequest:
    return RestrictedInferenceRequest(
        request_id="request-1",
        task_id="task-1",
        tenant_id="tenant-1",
        operation=RestrictedInferenceOperation.SCORE_CHOICES,
        payload={"prompt": "fixed question", "choices": ["yes", "no"]},
        model_manifest_id="manifest-1",
        policy_hash="policy-sha256",
        deadline_epoch_ms=2_000_000_000_000,
        paths=("src/security/auth.py",),
        idempotency_key="idem-1",
    )


def _success_response(request: RestrictedInferenceRequest) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request.request_id,
        "task_id": request.task_id,
        "operation": request.operation.value,
        "status": "succeeded",
        "result": {
            "items": [
                {"choice": "yes", "score": 0.7},
                {"choice": "no", "score": 0.3},
            ],
            "engine": "sentence-transformers",
            "model_id": "fixed-choice-model",
            "manifest_digest": "a" * 64,
            "latency_ms": 4.2,
        },
        "error": None,
        "no_generation": True,
    }


class _Transport:
    def __init__(self, response_factory=_success_response) -> None:
        self.response_factory = response_factory
        self.last_envelope: Mapping[str, Any] | None = None

    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        self.last_envelope = envelope
        request = RestrictedInferenceRequest.from_dict(envelope)
        return self.response_factory(request)


def test_request_roundtrip_is_json_safe_and_payload_is_immutable() -> None:
    original = _request()
    restored = RestrictedInferenceRequest.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()
    with pytest.raises(TypeError):
        restored.payload["prompt"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        restored.payload["choices"][0] = "mutated"  # type: ignore[index]


def test_request_rejects_unknown_payload_fields() -> None:
    raw = _request().to_dict()
    raw["payload"]["temperature"] = 0.8

    with pytest.raises(RestrictedInferenceContractError) as exc_info:
        RestrictedInferenceRequest.from_dict(raw)

    assert exc_info.value.reason_code == "invalid_payload_shape"


def test_request_rejects_duplicate_choices() -> None:
    raw = _request().to_dict()
    raw["payload"]["choices"] = ["yes", "yes"]

    with pytest.raises(RestrictedInferenceContractError) as exc_info:
        RestrictedInferenceRequest.from_dict(raw)

    assert exc_info.value.reason_code == "invalid_payload"


def test_contract_port_validates_and_correlates_worker_response() -> None:
    transport = _Transport()
    response = ContractRestrictedInferencePort(transport).execute(_request())

    assert response.status is RestrictedInferenceStatus.SUCCEEDED
    assert response.no_generation is True
    assert transport.last_envelope is not None


def test_contract_port_rejects_response_id_mismatch() -> None:
    def mismatched(request: RestrictedInferenceRequest) -> dict[str, Any]:
        response = _success_response(request)
        response["request_id"] = "different-request"
        return response

    with pytest.raises(RestrictedInferenceContractError) as exc_info:
        ContractRestrictedInferencePort(_Transport(mismatched)).execute(_request())

    assert exc_info.value.reason_code == "response_correlation_mismatch"


def test_response_rejects_generation_flag_and_generation_field() -> None:
    request = _request()
    response = _success_response(request)
    response["no_generation"] = False
    with pytest.raises(RestrictedInferenceContractError) as flag_error:
        RestrictedInferenceResponse.from_dict(response)
    assert flag_error.value.reason_code == "generation_boundary_violation"

    response = _success_response(request)
    response["result"]["items"][0]["generated_text"] = "invented answer"
    with pytest.raises(RestrictedInferenceContractError) as field_error:
        RestrictedInferenceResponse.from_dict(response)
    assert field_error.value.reason_code == "generation_field_forbidden"


def test_failed_response_requires_typed_error_and_no_result() -> None:
    response = RestrictedInferenceResponse.from_dict(
        {
            "contract_version": CONTRACT_VERSION,
            "request_id": "request-1",
            "task_id": "task-1",
            "operation": "embed",
            "status": "failed",
            "result": None,
            "error": {"code": "timeout", "message": "deadline exceeded", "retryable": True},
            "no_generation": True,
        }
    )

    assert response.error is not None
    assert response.error.code == "timeout"


def test_hub_contract_import_does_not_import_optional_ml_libraries() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    script = """
import sys
import agent.services.restricted_inference_contract
import agent.services.restricted_inference_port
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
