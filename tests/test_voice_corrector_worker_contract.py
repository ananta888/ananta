from __future__ import annotations

import time

import pytest

from ananta_contracts.voice_corrector_worker import (
    CONTRACT_VERSION,
    VoiceCorrectorContractError,
    VoiceCorrectorWorkerRequest,
    VoiceCorrectorWorkerResponse,
    build_edits,
)


def _request(*, max_edit_ratio: float = 0.5) -> VoiceCorrectorWorkerRequest:
    return VoiceCorrectorWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        original_text="hallo welt",
        model_id="gemma-2b-it",
        language="de",
        max_edit_ratio=max_edit_ratio,
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def test_contract_preserves_original_and_round_trips_replayable_edits() -> None:
    request = _request()
    corrected = "Hallo Welt."
    response = VoiceCorrectorWorkerResponse(
        request_id=request.request_id,
        task_id=request.task_id,
        status="corrected",
        original_text=request.original_text,
        corrected_text=corrected,
        edits=build_edits(request.original_text, corrected),
        reason_code=None,
        model_id=request.model_id,
        model_revision="sha256-deadbeef",
        engine_id="fixture-engine",
        prompt_version="prompt-v1",
    )

    parsed_request = VoiceCorrectorWorkerRequest.from_dict(request.to_dict())
    parsed_response = VoiceCorrectorWorkerResponse.from_dict(response.to_dict())
    parsed_response.validate_for(parsed_request)

    assert parsed_response.original_text == "hallo welt"
    assert parsed_response.corrected_text == corrected
    assert parsed_response.to_dict()["execution_owner"] == "worker"


def test_contract_rejects_unbounded_or_unprovenanced_rewrites() -> None:
    request = _request(max_edit_ratio=0.1)
    corrected = "vollständig anderer Inhalt"
    response = VoiceCorrectorWorkerResponse(
        request_id=request.request_id,
        task_id=request.task_id,
        status="corrected",
        original_text=request.original_text,
        corrected_text=corrected,
        edits=build_edits(request.original_text, corrected),
        reason_code=None,
        model_id=request.model_id,
        model_revision="revision-1",
        engine_id="fixture-engine",
        prompt_version="prompt-v1",
    )
    with pytest.raises(VoiceCorrectorContractError, match="edit-ratio"):
        response.validate_for(request)

    payload = response.to_dict()
    payload["original_text"] = "invented"
    payload["contract_version"] = CONTRACT_VERSION
    with pytest.raises(VoiceCorrectorContractError):
        VoiceCorrectorWorkerResponse.from_dict(payload)


def test_contract_rejects_json_type_coercion_at_the_worker_boundary() -> None:
    request = _request()
    raw_request = request.to_dict()
    raw_request["request_id"] = 123
    with pytest.raises(VoiceCorrectorContractError, match="request_id"):
        VoiceCorrectorWorkerRequest.from_dict(raw_request)

    response = VoiceCorrectorWorkerResponse(
        request_id=request.request_id,
        task_id=request.task_id,
        status="unchanged",
        original_text=request.original_text,
        corrected_text=request.original_text,
        edits=(),
        reason_code=None,
        model_id=request.model_id,
        model_revision="revision-1",
        engine_id="fixture-engine",
        prompt_version="prompt-v1",
    )
    raw_response = response.to_dict()
    raw_response["status"] = 7
    with pytest.raises(VoiceCorrectorContractError, match="status"):
        VoiceCorrectorWorkerResponse.from_dict(raw_response)
