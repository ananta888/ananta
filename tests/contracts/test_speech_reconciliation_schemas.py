from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from ananta_contracts.speech_reconciliation import (
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationContractError,
    SpeechReconciliationJob,
    SpeechResourceVector,
)
from ananta_contracts.speech_reconciliation_worker import SpeechReconciliationPass
from tests.speech_reconciliation_support import checkpoint_payload, job_contract, job_payload, result_payload

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("speech_reconciliation_job.v1.json", job_payload()),
        ("speech_reconciliation_checkpoint.v1.json", checkpoint_payload(job_contract())),
        ("speech_reconciliation_result.v1.json", result_payload(job_contract())),
    ],
)
def test_wire_payloads_validate_against_closed_json_schemas(filename, payload) -> None:
    schema = json.loads((ROOT / "schemas/voice" / filename).read_text())
    jsonschema.Draft202012Validator(schema).validate(payload)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate({**payload, "unknown": True})


def test_budget_schema_and_python_reject_negative_overflow_and_inconsistent_arithmetic() -> None:
    vector = SpeechResourceVector(wall_time_ms=100)
    payload = {
        "contract_version": "ananta.speech-reconciliation.v1",
        "job_id": "job-test",
        "attempt_id": "attempt-test",
        "fencing_epoch": 1,
        "sequence": 0,
        "stage": "slow_asr",
        "source_duration_ms": 1000,
        "compute_factor": 1,
        "allocated": vector.to_dict(),
        "reserved": SpeechResourceVector().to_dict(),
        "consumed": SpeechResourceVector().to_dict(),
        "remaining": vector.to_dict(),
    }
    schema = json.loads((ROOT / "schemas/voice/speech_reconciliation_budget_ledger.v1.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(payload)
    assert SpeechReconciliationBudgetLedger.from_mapping(payload).sequence == 0
    for invalid in (-1, 2**63):
        changed = {**payload, "allocated": {**payload["allocated"], "cpu_time_ms": invalid}}
        with pytest.raises((jsonschema.ValidationError, SpeechReconciliationContractError)):
            jsonschema.Draft202012Validator(schema).validate(changed)
            SpeechReconciliationBudgetLedger.from_mapping(changed)
    with pytest.raises(SpeechReconciliationContractError, match="speech_reconciliation_budget_arithmetic_invalid"):
        SpeechReconciliationBudgetLedger.from_mapping(
            {**payload, "remaining": {**payload["remaining"], "wall_time_ms": 99}}
        )


def test_research_factor_requires_separate_policy_reference() -> None:
    with pytest.raises(SpeechReconciliationContractError, match="speech_reconciliation_research_policy_required"):
        SpeechReconciliationJob.from_mapping(job_payload(max_compute_factor=21))
    admitted = SpeechReconciliationJob.from_mapping(
        job_payload(max_compute_factor=21, research_policy_ref="artifact://speech-policies/test/research-v1")
    )
    assert admitted.max_compute_factor == 21


def test_unknown_stage_is_rejected_by_shared_python_and_worker_contract() -> None:
    with pytest.raises(SpeechReconciliationContractError, match="speech_reconciliation_stage_invalid"):
        SpeechReconciliationJob.from_mapping(job_payload(stage="invented_stage"))


def test_worker_pass_accepts_pinned_upstream_revision_identity() -> None:
    value = SpeechReconciliationPass.from_mapping(
        {
            "pass_id": "whisper-cpp-primary",
            "model_id": "whisper-cpp-v1.8.6-ggml-small",
            "model_revision": "whisper.cpp@23ee035+ggml-small@90a64d8",
            "variant_id": "original",
            "language": "de",
        }
    )

    assert value.model_revision == "whisper.cpp@23ee035+ggml-small@90a64d8"
