from __future__ import annotations

import json

import pytest

from agent.adapters.spreadsheet_mock_execution_adapter import DeterministicSpreadsheetMockExecutionAdapter
from agent.services.ml_intern_lora_inference_contract import LoraInferenceResult
from agent.services.ml_intern_provenance_contract import MlInternTrainingContractError
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.spreadsheet_evaluation_service import SpreadsheetEvaluationService
from agent.services.spreadsheet_inference_service import SpreadsheetInferenceService
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy
from tests.spreadsheet_studio.helpers import service, snapshot


def _action_output() -> dict:
    return {
        "schema": "ananta.spreadsheet-action-output.v1",
        "actions": [
            {
                "action_id": "action-one",
                "kind": "set_value",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "value": 42,
                "formula": None,
            }
        ],
    }


def test_strategy_accepts_only_closed_actions_or_bounded_refusal() -> None:
    strategy = SpreadsheetTrainingTaskFamilyStrategy()
    parsed = strategy.parse_inference(json.dumps(_action_output()))
    assert parsed["actions"][0]["value"] == 42
    refusal = strategy.parse_inference(
        json.dumps(
            {
                "schema": "ananta.spreadsheet-action-refusal.v1",
                "reason_code": "spreadsheet_request_unsafe",
            }
        )
    )
    assert strategy.score_output(json.dumps(refusal))["safe_rejection"] is True
    with pytest.raises(ValueError, match="fields_invalid"):
        strategy.parse_inference(json.dumps({**_action_output(), "auto_apply": True}))
    with pytest.raises(ValueError, match="formula_op_invalid"):
        unsafe = _action_output()
        unsafe["actions"][0].update(kind="set_formula", value=None, formula={"op": "python", "code": "x"})
        strategy.parse_inference(json.dumps(unsafe))


def test_ml_intern_training_contract_additively_binds_spreadsheet_family() -> None:
    strategy = SpreadsheetTrainingTaskFamilyStrategy()
    command = CreateTrainingJobCommand.from_mapping(
        {
            "dataset_id": "dataset-one",
            "job_type": "train_lora",
            "mode": "dry_run",
            "backend": "mock",
            "base_model": "model-one",
            "task_family": "spreadsheet_actions",
            "task_kinds": ["spreadsheet_actions"],
            "output_schema_digest": strategy.schema_digest,
            "serializer_digest": strategy.serializer_digest,
        }
    )
    assert command.request_spec["task_family"] == "spreadsheet_actions"
    assert command.request_spec["task_kinds"] == ["spreadsheet_actions"]
    with pytest.raises(MlInternTrainingContractError) as captured:
        CreateTrainingJobCommand.from_mapping(
            {
                "dataset_id": "dataset-one",
                "job_type": "train_lora",
                "mode": "dry_run",
                "backend": "mock",
                "base_model": "model-one",
                "task_family": "spreadsheet_actions",
                "task_kinds": ["generic_text"],
                "output_schema_digest": strategy.schema_digest,
                "serializer_digest": strategy.serializer_digest,
            }
        )
    assert captured.value.reason_code == "training_task_kinds_invalid"
    live = CreateTrainingJobCommand.from_mapping(
        {
            "dataset_id": "dataset-one",
            "job_type": "train_lora",
            "mode": "live",
            "backend": "unsloth",
            "base_model": "model-one",
            "task_family": "spreadsheet_actions",
            "task_kinds": ["spreadsheet_actions"],
            "output_schema_digest": strategy.schema_digest,
            "serializer_digest": strategy.serializer_digest,
            "training_admission_digest": "a" * 64,
            "live_confirmed": True,
            "risk_reason": "automatic governed spreadsheet training",
        }
    )
    assert live.request_spec["training_admission_digest"] == "a" * 64
    with pytest.raises(MlInternTrainingContractError) as missing_admission:
        CreateTrainingJobCommand.from_mapping(
            {
                "dataset_id": "dataset-one",
                "job_type": "train_lora",
                "mode": "live",
                "backend": "unsloth",
                "base_model": "model-one",
                "task_family": "spreadsheet_actions",
                "task_kinds": ["spreadsheet_actions"],
                "output_schema_digest": strategy.schema_digest,
                "serializer_digest": strategy.serializer_digest,
                "live_confirmed": True,
                "risk_reason": "automatic governed spreadsheet training",
            }
        )
    assert missing_admission.value.reason_code == "training_admission_required"


def test_inference_facade_returns_actions_without_applying_them(tmp_path) -> None:
    studio = service(tmp_path / "inference.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-one",
    )

    class Inference:
        def generate(self, request, *, tenant_id, owner_subject):
            assert request.task_kind == "spreadsheet_actions"
            assert tenant_id == "tenant-a"
            assert owner_subject == "user-a"
            return LoraInferenceResult(
                text=json.dumps(_action_output()),
                worker_id="worker-one",
                capability="ml_intern_lora_text_generation",
                adapter_id="adapter-one",
                adapter_version="version-one",
                reason_code="approved_adapter_inference_succeeded",
            )

    result = SpreadsheetInferenceService(documents=studio, inference=Inference()).propose_actions(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload={
            "schema": "ananta.spreadsheet-inference-command.v1",
            "document_id": "document-one",
            "instruction": "Set A1 to 42",
            "adapter_id": "adapter-one",
            "adapter_version": "version-one",
            "base_model": "model-one",
            "task_id": "task-one",
            "max_new_tokens": 256,
            "temperature": 0.0,
        },
    )
    assert result["result"]["actions"][0]["value"] == 42
    assert result["automatic_apply"] is False
    assert (
        studio.get_document(tenant_id="tenant-a", document_id="document-one", principal_id="user-a")["version"]
        == document["version"]
    )


def test_execution_backed_evaluation_never_publishes_candidates() -> None:
    evaluator = SpreadsheetEvaluationService(
        executor=DeterministicSpreadsheetMockExecutionAdapter(),
        policy=SpreadsheetPolicy(enabled=True, mode="mock", automatic_promotion_enabled=False),
        clock=lambda: 10.0,
    )
    normal = {
        "sample_id": "normal",
        "snapshot": snapshot(),
        "validators": [
            {
                "validator_id": "validator-one",
                "kind": "equals",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "expected": 42,
                "minimum": None,
                "maximum": None,
            }
        ],
        "safe_refusal_expected": False,
    }
    unsafe = {
        "sample_id": "unsafe",
        "snapshot": snapshot(),
        "validators": [],
        "safe_refusal_expected": True,
    }
    refusal = json.dumps(
        {
            "schema": "ananta.spreadsheet-action-refusal.v1",
            "reason_code": "spreadsheet_request_unsafe",
        }
    )
    report = evaluator.evaluate(
        samples=[normal, unsafe],
        base_output=lambda sample: refusal if sample["safe_refusal_expected"] else "not-json",
        adapter_output=lambda sample: refusal if sample["safe_refusal_expected"] else json.dumps(_action_output()),
    )
    assert report["adapter_admitted"] is True
    assert report["summary"]["adapter"]["action_valid_rate"] == 1.0
    assert report["summary"]["adapter"]["safe_rejection_rate"] == 1.0
    assert report["summary"]["adapter"]["safe_rejection_case_count"] == 1
    assert report["bindings"]["engine_version"] == "spreadsheet-execution-evaluation.v2"
    assert report["published_candidates"] == 0
    assert report["feedback_events"] == 0
    assert report["consent_events"] == 0
