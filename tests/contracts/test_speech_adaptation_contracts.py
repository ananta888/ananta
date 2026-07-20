from __future__ import annotations

import copy
import math

import pytest

from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    TRAIN_JOB_TYPE,
    SpeechAdaptationContractError,
    SpeechAdaptationJob,
    SpeechAdaptationResult,
    speech_budget_digest,
)
from tests.speech_adaptation_support import digest, speech_job_payload
from worker.training.contracts import CONTRACT_VERSION as TEXT_LORA_CONTRACT_VERSION


def test_speech_contract_is_closed_and_distinct_from_text_lora() -> None:
    payload = speech_job_payload()
    job = SpeechAdaptationJob.from_mapping(payload, now_ms=1_000_000)

    assert job.contract_version == CONTRACT_VERSION == "ananta.speech-adaptation.v1"
    assert job.job_type == TRAIN_JOB_TYPE == "speech_adaptation_train"
    assert CONTRACT_VERSION != TEXT_LORA_CONTRACT_VERSION
    assert job.dataset.immutable is True
    assert job.configuration.backend_digest == digest("mock-backend-v1")


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (lambda value: value.update({"unexpected": True}), "speech_contract_unknown_field"),
        (lambda value: value["scope"].update({"scope_digest": digest("wrong")}), "speech_scope_digest_mismatch"),
        (lambda value: value.update({"binding_digest": digest("wrong")}), "speech_job_binding_digest_mismatch"),
        (lambda value: value["dataset"].update({"immutable": False}), "speech_dataset_not_immutable"),
    ],
)
def test_contract_rejects_unknown_mutable_and_digest_mismatches(mutation, reason_code: str) -> None:
    payload = speech_job_payload()
    mutation(payload)
    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationJob.from_mapping(payload, now_ms=1_000_000)
    assert captured.value.reason_code == reason_code


def test_contract_rejects_non_finite_and_stale_deadline() -> None:
    payload = speech_job_payload()
    payload["configuration"]["learning_rate"] = math.inf
    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationJob.from_mapping(payload, now_ms=1_000_000)
    assert captured.value.reason_code == "speech_contract_limit_exceeded"

    stale = speech_job_payload()
    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationJob.from_mapping(stale, now_ms=stale["deadline_at_ms"])
    assert captured.value.reason_code == "speech_deadline_stale"


def test_wall_time_budget_must_fit_the_immutable_capacity_lease() -> None:
    payload = speech_job_payload()
    payload["budget"]["max_wall_seconds"] = 55
    payload["budget"]["budget_digest"] = speech_budget_digest(payload["budget"])

    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationJob.from_mapping(payload, now_ms=1_000_000)

    assert captured.value.reason_code == "speech_budget_lease_mismatch"


def test_resume_is_all_or_nothing_and_bound_to_current_job() -> None:
    payload = speech_job_payload(max_steps=4)
    payload["resume"] = {"checkpoint_ref": "artifact://speech-checkpoints/test/checkpoint"}
    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationJob.from_mapping(payload, now_ms=1_000_000)
    assert captured.value.reason_code == "speech_contract_digest_invalid"

    complete = copy.deepcopy(speech_job_payload(max_steps=4))
    complete["resume"] = {
        "checkpoint_ref": "artifact://speech-checkpoints/test/checkpoint",
        "checkpoint_digest": digest("checkpoint"),
        "checkpoint_step": 2,
        "source_attempt_digest": digest("source-attempt"),
        "dataset_digest": complete["dataset"]["dataset_digest"],
        "split_digest": complete["dataset"]["split_digest"],
        "model_digest": complete["base_model"]["model_digest"],
        "scope_digest": complete["scope"]["scope_digest"],
        "config_digest": digest("wrong-config"),
    }
    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationJob.from_mapping(complete, now_ms=1_000_000)
    assert captured.value.reason_code == "speech_resume_binding_mismatch"


def test_completed_result_requires_evaluation_and_checkpoint_evidence() -> None:
    job = SpeechAdaptationJob.from_mapping(speech_job_payload(), now_ms=1_000_000)
    payload = {
        "contract_version": CONTRACT_VERSION,
        "result_type": "speech_adaptation_result",
        "job_id": job.job_id,
        "attempt_id": job.attempt.attempt_id,
        "binding_digest": job.binding_digest,
        "fencing_digest": job.fencing.fencing_digest,
        "status": "completed",
        "events_digest": digest("events"),
        "evaluation_report_digest": None,
        "checkpoint_digest": digest("checkpoint"),
        "artifact": {
            "artifact_id": job.artifact_target.target_id,
            "artifact_ref": job.artifact_target.artifact_ref,
            "sha256": digest("artifact"),
            "size_bytes": 64,
            "media_type": "application/vnd.ananta.speech-adapter",
        },
        "reason_code": None,
    }

    with pytest.raises(SpeechAdaptationContractError) as captured:
        SpeechAdaptationResult.from_mapping(payload)

    assert captured.value.reason_code == "speech_result_evidence_missing"
