from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.adapters.spreadsheet_mock_execution_adapter import DeterministicSpreadsheetMockExecutionAdapter
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.spreadsheet_adapter_admission_service import SpreadsheetAdapterAdmissionService
from agent.services.spreadsheet_evaluation_service import SpreadsheetEvaluationService
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy
from ananta_contracts.spreadsheet_studio import canonical_digest
from tests.spreadsheet_studio.helpers import snapshot


def _governance() -> dict[str, str]:
    strategy = SpreadsheetTrainingTaskFamilyStrategy()
    value = {
        "training_profile_digest": "1" * 64,
        "base_model_digest": "2" * 64,
        "dataset_manifest_digest": "3" * 64,
        "dataset_artifact_digest": "4" * 64,
        "dataset_recipe_digest": "5" * 64,
        "split_lock_digest": "6" * 64,
        "action_schema_digest": strategy.schema_digest,
        "serializer_digest": strategy.serializer_digest,
        "policy_digest": "7" * 64,
        "resource_profile_digest": "8" * 64,
        "training_admission_digest": "9" * 64,
    }
    return {**value, "governance_digest": canonical_digest(value)}


def _action_output() -> str:
    return json.dumps(
        {
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
    )


def _refusal() -> str:
    return json.dumps(
        {
            "schema": "ananta.spreadsheet-action-refusal.v1",
            "reason_code": "spreadsheet_request_unsafe",
        }
    )


def _samples() -> list[dict]:
    dimensions = {
        "task_kind": "spreadsheet_actions",
        "file_format": "xlsx",
        "size_bucket": "small",
        "locale": "de-DE",
        "template_cluster": "budget-v1",
        "security_class": "ordinary",
    }
    return [
        {
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
            "dimensions": dimensions,
            "expected_changed_cells": ["sheet-one!A1"],
        },
        {
            "sample_id": "unsafe",
            "snapshot": snapshot(),
            "validators": [],
            "safe_refusal_expected": True,
            "dimensions": {**dimensions, "security_class": "unsafe"},
            "expected_changed_cells": [],
        },
    ]


def _model_output(sample: dict) -> dict:
    return {
        "text": _refusal() if sample["safe_refusal_expected"] else _action_output(),
        "resource_usage": {"cpu_time_ms": 2, "peak_memory_bytes": 1024, "tokens": 64},
    }


def _report(governance: dict[str, str]) -> dict:
    return SpreadsheetEvaluationService(
        executor=DeterministicSpreadsheetMockExecutionAdapter(),
        policy=SpreadsheetPolicy(enabled=True, mode="mock", automatic_promotion_enabled=False),
        clock=lambda: 10.0,
    ).evaluate(
        samples=_samples(),
        base_output=_model_output,
        adapter_output=_model_output,
        bindings={
            "evaluation_id": "evaluation-one",
            "adapter_id": "adapter-one",
            "base_model_id": "local/base",
            "base_model_digest": governance["base_model_digest"],
            "adapter_digest": "a" * 64,
            "dataset_manifest_digest": governance["dataset_manifest_digest"],
            "dataset_artifact_digest": governance["dataset_artifact_digest"],
            "dataset_recipe_digest": governance["dataset_recipe_digest"],
            "split_lock_digest": governance["split_lock_digest"],
            "training_profile_digest": governance["training_profile_digest"],
            "training_admission_digest": governance["training_admission_digest"],
            "training_governance_digest": governance["governance_digest"],
            "training_policy_digest": governance["policy_digest"],
            "resource_profile_digest": governance["resource_profile_digest"],
            "runtime_digest": "b" * 64,
        },
    )


def _registry(tmp_path: Path, governance: dict[str, str]) -> MlInternAdapterRegistryService:
    registry = MlInternAdapterRegistryService(tmp_path / "registry.json")
    registry.register_trained(
        adapter_id="adapter-one",
        display_name="Spreadsheet adapter",
        version="v1",
        base_model="local/base",
        method="qlora",
        artifact_paths={"adapter_dir": "adapters/adapter-one"},
        config_hash="c" * 64,
        artifact_sha256="a" * 64,
        dataset_hash=governance["dataset_artifact_digest"],
        source_ids=["SRC_0003"],
        run_ids=["RUN_0001"],
        provenance_verified=True,
        task_kinds=["spreadsheet_actions"],
        tenant_id="tenant-a",
        owner_subject="user-a",
    )
    return registry


def _command(governance: dict[str, str], report: dict) -> dict:
    return {
        "schema": "ananta.spreadsheet-adapter-admission-command.v1",
        "adapter_id": "adapter-one",
        "expected_registry_version": 1,
        "evaluation_report": report,
        "training_governance": governance,
        "execution_evidence": {
            "job_id": "job-one",
            "attempt_id": "attempt-one",
            "fencing_token_digest": "d" * 64,
            "source_ids": ["SRC_0003"],
            "run_ids": ["RUN_0001"],
            "export_sha256": "e" * 64,
        },
        "reason": "automatic execution-backed Spreadsheet adapter admission",
    }


def test_grouped_execution_report_drives_atomic_adapter_admission_and_replay(tmp_path: Path) -> None:
    governance = _governance()
    report = _report(governance)
    registry = _registry(tmp_path, governance)
    service = SpreadsheetAdapterAdmissionService(registry=registry)

    assert report["schema"] == "ananta.spreadsheet-evaluation-report.v2"
    assert report["adapter_admitted"] is True
    assert report["coverage"] == {
        "group_dimensions_complete": True,
        "expected_diff_complete": True,
        "resource_usage_complete": True,
    }
    assert report["summary"]["adapter"]["cell_diff_precision"] == 1.0
    assert {group["dimension"] for group in report["groups"]} >= {
        "task_kind",
        "file_format",
        "locale",
        "failure_class",
    }
    report_schema = json.loads(Path("schemas/spreadsheet-studio/evaluation-report.v2.json").read_text())
    Draft202012Validator(report_schema).validate(report)

    first = service.admit(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload=_command(governance, report),
        idempotency_key="adapter-admission-one",
    )
    replay = service.admit(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload=_command(governance, report),
        idempotency_key="adapter-admission-one",
    )

    assert first["status"] == "approved"
    assert first["automatic_apply"] is False
    assert first["human_intervention_required"] is False
    admission_schema = json.loads(Path("schemas/spreadsheet-studio/adapter-admission.v1.json").read_text())
    Draft202012Validator(admission_schema).validate(first)
    assert replay["replayed"] is True
    stored = registry.get("adapter-one", tenant_id="tenant-a", owner_subject="user-a")
    assert stored is not None
    assert stored.status == "approved"
    evidence_metrics = stored.promotion_history[0]["evidence"]["metrics"]
    assert evidence_metrics["spreadsheet_governance_digest"] == governance["governance_digest"]
    assert evidence_metrics["runtime_digest"] == "b" * 64


def test_tampered_or_regressing_evaluation_cannot_promote(tmp_path: Path) -> None:
    governance = _governance()
    report = _report(governance)
    registry = _registry(tmp_path, governance)
    service = SpreadsheetAdapterAdmissionService(registry=registry)

    tampered = {**report, "published_candidates": 1}
    with pytest.raises(ValueError, match="report_digest_mismatch"):
        service.admit(
            tenant_id="tenant-a",
            principal_id="user-a",
            payload=_command(governance, tampered),
            idempotency_key="adapter-admission-tampered",
        )

    failed = dict(report)
    failed.pop("report_digest")
    failed["adapter_admitted"] = False
    failed["reason_codes"] = ["spreadsheet_adapter_execution_success_gate_failed"]
    failed["report_digest"] = canonical_digest(failed)
    with pytest.raises(PermissionError, match="evaluation_gate_failed"):
        service.admit(
            tenant_id="tenant-a",
            principal_id="user-a",
            payload=_command(governance, failed),
            idempotency_key="adapter-admission-failed",
        )
