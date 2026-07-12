from __future__ import annotations

import time

import pytest

from ananta_contracts.generative_judge_worker import (
    CONTRACT_VERSION,
    GenerativeJudgeCandidate,
    GenerativeJudgeContractError,
    GenerativeJudgeWorkerRequest,
    GenerativeJudgeWorkerResponse,
)


def _request() -> GenerativeJudgeWorkerRequest:
    return GenerativeJudgeWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        candidates=(
            GenerativeJudgeCandidate("candidate-000", "baseline"),
            GenerativeJudgeCandidate("candidate-001", "alternative"),
        ),
        baseline_choice_id="candidate-000",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def test_contract_round_trip_contains_choice_ids_but_response_contains_no_text() -> None:
    request = _request()
    response = GenerativeJudgeWorkerResponse(
        request_id=request.request_id,
        task_id=request.task_id,
        status="selected",
        choice_id="candidate-001",
        reason_code=None,
        engine_id="fixture-engine",
    )

    parsed_request = GenerativeJudgeWorkerRequest.from_dict(request.to_dict())
    parsed_response = GenerativeJudgeWorkerResponse.from_dict(response.to_dict())
    parsed_response.validate_for(parsed_request)

    assert response.to_dict()["execution_owner"] == "worker"
    assert "text" not in response.to_dict()


def test_contract_rejects_unknown_fields_and_unprovenanced_selection() -> None:
    request_payload = _request().to_dict()
    request_payload["orchestration"] = {"next_worker": "forbidden"}
    with pytest.raises(GenerativeJudgeContractError):
        GenerativeJudgeWorkerRequest.from_dict(request_payload)

    response = GenerativeJudgeWorkerResponse.from_dict(
        {
            "contract_version": CONTRACT_VERSION,
            "request_id": "request-1",
            "task_id": "task-1",
            "status": "selected",
            "choice_id": "invented",
            "reason_code": None,
            "engine_id": "fixture-engine",
            "execution_owner": "worker",
        }
    )
    with pytest.raises(GenerativeJudgeContractError, match="unknown candidate"):
        response.validate_for(_request())
