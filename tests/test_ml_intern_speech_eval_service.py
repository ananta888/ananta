from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.ml_intern_speech_eval_service import MlInternSpeechEvalService
from tests.speech_adaptation_support import speech_job
from worker.speech_training.evaluation import SpeechEvaluationError, build_mock_evaluation


def _bindings(job):
    return {
        "dataset_digest": job.dataset.dataset_digest,
        "split_digest": job.dataset.split_digest,
        "model_digest": job.base_model.model_digest,
        "config_digest": job.configuration.config_digest,
        "scope_digest": job.scope.scope_digest,
        "consent_digest": job.consent.consent_digest,
    }


def test_evaluation_requires_every_variant_metric_and_safety_probe() -> None:
    job = speech_job()
    report = build_mock_evaluation(job)
    decision = MlInternSpeechEvalService().decide(report, expected_bindings=_bindings(job))
    assert decision.passed is True
    assert decision.approval_eligible is False
    assert "speech_evaluation_mock_has_no_quality_claim" in decision.reason_codes
    assert set(report["metrics"]["intelligibility"]["values"]) == {
        "generic",
        "local_only",
        "reconciled",
        "adapted",
    }

    missing = copy.deepcopy(report)
    del missing["probes"]["memorization_canary"]
    with pytest.raises(SpeechEvaluationError) as captured:
        MlInternSpeechEvalService().decide(missing, expected_bindings=_bindings(job))
    assert captured.value.reason_code == "speech_evaluation_probe_missing"


def test_partial_failure_and_binding_mismatch_are_not_passed() -> None:
    job = speech_job()
    report = build_mock_evaluation(job, force_failure=True)
    decision = MlInternSpeechEvalService().decide(report, expected_bindings=_bindings(job))
    assert decision.passed is False
    assert decision.approval_eligible is False

    mismatch = _bindings(job)
    mismatch["dataset_digest"] = "0" * 64
    with pytest.raises(SpeechEvaluationError) as captured:
        MlInternSpeechEvalService().decide(build_mock_evaluation(job), expected_bindings=mismatch)
    assert captured.value.reason_code == "speech_evaluation_binding_mismatch"


def test_published_json_schema_accepts_the_worker_report() -> None:
    schema = json.loads(Path("schemas/voice/speech_adaptation_evaluation.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(build_mock_evaluation(speech_job()))
