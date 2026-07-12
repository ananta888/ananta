from __future__ import annotations

import time

import pytest

from agent.services.restricted_inference_contract import (
    RestrictedInferenceContractError,
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
)


def _request(*, payload=None, execution_policy=None) -> RestrictedInferenceRequest:
    return RestrictedInferenceRequest(
        request_id="request-1",
        task_id="task-1",
        run_id="run-1",
        tenant_id="tenant-1",
        operation=RestrictedInferenceOperation.RERANK,
        payload=payload
        or {
            "query": "query",
            "candidates": [{"record_id": "a", "path": "a.py", "excerpt": "a"}],
        },
        model_manifest_id="manifest-1",
        policy_hash="policy-1",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
        execution_policy=execution_policy or {},
    )


def test_execution_policy_is_frozen_normalized_and_roundtrips() -> None:
    request = _request(execution_policy={"max_candidates": 2, "device": "CPU"})
    restored = RestrictedInferenceRequest.from_dict(request.to_dict())

    assert restored.run_id == "run-1"
    assert restored.execution_policy["device"] == "cpu"
    assert restored.execution_policy["max_candidates"] == 2
    with pytest.raises(TypeError):
        restored.execution_policy["device"] = "cuda"  # type: ignore[index]


def test_legacy_envelope_without_optional_run_and_policy_fields_remains_valid() -> None:
    envelope = _request().to_dict()
    envelope.pop("run_id")
    envelope.pop("execution_policy")

    restored = RestrictedInferenceRequest.from_dict(envelope)

    assert restored.run_id == ""
    assert restored.execution_policy["allow_cpu_fallback"] is False


def test_candidate_batch_and_unknown_execution_fields_fail_closed() -> None:
    with pytest.raises(RestrictedInferenceContractError) as candidates_error:
        _request(
            payload={
                "query": "query",
                "candidates": [{"record_id": str(index), "path": f"{index}.py", "excerpt": "x"} for index in range(3)],
            },
            execution_policy={"max_candidates": 2},
        )
    assert candidates_error.value.reason_code == "candidate_limit_exceeded"

    with pytest.raises(RestrictedInferenceContractError) as unknown_error:
        _request(execution_policy={"temperature": 1})
    assert unknown_error.value.reason_code == "invalid_execution_policy"
