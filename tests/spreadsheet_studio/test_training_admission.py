from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.spreadsheet_learning_store import SpreadsheetLearningStore
from agent.services.spreadsheet_training_admission_service import SpreadsheetTrainingAdmissionService
from ananta_contracts.spreadsheet_studio import canonical_digest


def _report(*, sample_count: int = 20, rate: float = 1.0) -> dict:
    metrics = {
        "schema_valid_rate": rate,
        "action_valid_rate": rate,
        "safe_rejection_rate": rate,
        "safe_rejection_case_count": 2,
        "safe_policy_rate": rate,
        "execution_success_rate": rate,
        "validator_pass_rate": rate,
        "unintended_change_rate": 0.0 if rate == 1.0 else 1.0,
        "score": rate,
    }
    report = {
        "schema": "ananta.spreadsheet-evaluation-report.v1",
        "mode": "non_publishing",
        "summary": {"sample_count": sample_count, "base": metrics, "adapter": metrics},
        "samples": [],
        "adapter_admitted": False,
        "reason_codes": ["spreadsheet_adapter_evaluation_gate_failed"],
        "bindings": {
            "engine_version": "spreadsheet-execution-evaluation.v2",
            "sample_digest": "1" * 64,
            "policy_digest": "2" * 64,
            "output_schema_digest": "3" * 64,
            "serializer_digest": "4" * 64,
        },
        "duration_ms": 10,
        "published_candidates": 0,
        "feedback_events": 0,
        "consent_events": 0,
        "human_intervention_required": False,
    }
    report["report_digest"] = canonical_digest(report)
    return report


def _dataset() -> dict:
    return {
        "dataset_id": "dataset-one",
        "dataset_digest": "5" * 64,
        "digest": "6" * 64,
        "record_count": 100,
        "split_counts": {"train": 70, "validation": 10, "eval": 10, "test": 10},
        "consent_refs": [{"consent_id": f"consent-{index}"} for index in range(100)],
        "masking_version": "spreadsheet-masking.v1",
        "recipe_manifest": {
            "license_policy": "owner-submitted-consent.v1",
            "tenant_pooling": "forbidden",
            "task_kinds": ["spreadsheet_actions"],
        },
        "split_lock": {
            "split_lock_digest": "7" * 64,
            "diversity": {
                "lineage_root_count": 10,
                "instruction_template_count": 10,
                "formula_family_count": 5,
                "leakage_cluster_count": 10,
            },
        },
    }


def _resource() -> dict:
    value = {
        "schema": "ananta.spreadsheet-training-resource-profile.v1",
        "profile_id": "rtx3080-safe",
        "backend": "unsloth",
        "available": True,
        "supported_base_models": ["model-one"],
        "max_context_tokens": 4096,
        "gpu_memory_bytes": 10_000_000_000,
    }
    value["profile_digest"] = canonical_digest(value)
    return value


class Learning:
    def __init__(self) -> None:
        self.dataset = _dataset()

    def get_dataset(self, **_kwargs):
        return dict(self.dataset)


def _service(tmp_path: Path) -> tuple[SpreadsheetTrainingAdmissionService, Learning]:
    learning = Learning()
    return (
        SpreadsheetTrainingAdmissionService(
            learning=learning,
            repository=SpreadsheetLearningStore(tmp_path / "admission.sqlite3"),
            clock=lambda: 1_900_000_000.0,
        ),
        learning,
    )


def test_quantitative_go_is_digest_bound_and_schema_valid(tmp_path: Path) -> None:
    service, learning = _service(tmp_path)
    baseline = service.create_baseline(
        tenant_id="tenant-a",
        principal_id="owner-one",
        payload={
            "schema": "ananta.spreadsheet-baseline-command.v1",
            "baseline_id": "baseline-one",
            "base_model": "model-one",
            "model_digest": "8" * 64,
            "evaluation_report": _report(),
        },
    )
    admission = service.admit(
        tenant_id="tenant-a",
        principal_id="owner-one",
        payload={
            "schema": "ananta.spreadsheet-training-admission-command.v1",
            "admission_id": "admission-one",
            "dataset_id": "dataset-one",
            "baseline_id": baseline["baseline_id"],
            "base_model": "model-one",
            "resource_profile": _resource(),
        },
    )

    assert admission["decision"] == "go"
    assert admission["reason_codes"] == []
    assert admission["human_intervention_required"] is False
    assert service.require_go(
        tenant_id="tenant-a",
        principal_id="owner-one",
        admission_id="admission-one",
        dataset_id="dataset-one",
        base_model="model-one",
    )["admission_digest"] == admission["admission_digest"]
    for filename, value in (
        ("base-model-baseline.v1.json", baseline),
        ("training-admission.v1.json", admission),
    ):
        schema = json.loads((Path("schemas/spreadsheet-studio") / filename).read_text())
        Draft202012Validator(schema).validate(value)

    learning.dataset["dataset_digest"] = "9" * 64
    with pytest.raises(PermissionError, match="binding_stale"):
        service.require_go(
            tenant_id="tenant-a",
            principal_id="owner-one",
            admission_id="admission-one",
            dataset_id="dataset-one",
            base_model="model-one",
        )


def test_no_go_keeps_automatic_untrained_product_path_available(tmp_path: Path) -> None:
    service, learning = _service(tmp_path)
    learning.dataset["record_count"] = 10
    learning.dataset["consent_refs"] = learning.dataset["consent_refs"][:5]
    baseline = service.create_baseline(
        tenant_id="tenant-a",
        principal_id="owner-one",
        payload={
            "schema": "ananta.spreadsheet-baseline-command.v1",
            "baseline_id": "baseline-poor",
            "base_model": "model-one",
            "model_digest": "8" * 64,
            "evaluation_report": _report(sample_count=2, rate=0.5),
        },
    )
    admission = service.admit(
        tenant_id="tenant-a",
        principal_id="owner-one",
        payload={
            "schema": "ananta.spreadsheet-training-admission-command.v1",
            "admission_id": "admission-no-go",
            "dataset_id": "dataset-one",
            "baseline_id": baseline["baseline_id"],
            "base_model": "model-one",
            "resource_profile": _resource(),
        },
    )

    assert admission["decision"] == "no_go"
    assert "spreadsheet_dataset_minimum_records_not_met" in admission["reason_codes"]
    assert "spreadsheet_baseline_samples_threshold_failed" in admission["reason_codes"]
    assert admission["alternative_path"] == {
        "available": True,
        "mode": "base_model_only",
        "reason_code": "spreadsheet_training_not_required_for_product_path",
    }
    with pytest.raises(PermissionError, match="admission_no_go"):
        service.require_go(
            tenant_id="tenant-a",
            principal_id="owner-one",
            admission_id="admission-no-go",
            dataset_id="dataset-one",
            base_model="model-one",
        )


def test_tampered_baseline_and_resource_digests_fail_automatically(tmp_path: Path) -> None:
    service, _learning = _service(tmp_path)
    report = _report()
    report["duration_ms"] = 11
    with pytest.raises(ValueError, match="report_digest_mismatch"):
        service.create_baseline(
            tenant_id="tenant-a",
            principal_id="owner-one",
            payload={
                "schema": "ananta.spreadsheet-baseline-command.v1",
                "baseline_id": "baseline-tampered",
                "base_model": "model-one",
                "model_digest": "8" * 64,
                "evaluation_report": report,
            },
        )
    baseline = service.create_baseline(
        tenant_id="tenant-a",
        principal_id="owner-one",
        payload={
            "schema": "ananta.spreadsheet-baseline-command.v1",
            "baseline_id": "baseline-valid",
            "base_model": "model-one",
            "model_digest": "8" * 64,
            "evaluation_report": _report(),
        },
    )
    resource = _resource()
    resource["max_context_tokens"] = 8192
    with pytest.raises(ValueError, match="profile_digest_mismatch"):
        service.admit(
            tenant_id="tenant-a",
            principal_id="owner-one",
            payload={
                "schema": "ananta.spreadsheet-training-admission-command.v1",
                "admission_id": "admission-tampered",
                "dataset_id": "dataset-one",
                "baseline_id": baseline["baseline_id"],
                "base_model": "model-one",
                "resource_profile": resource,
            },
        )
