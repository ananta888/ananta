from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.spreadsheet_ml_intern_bridge_service import SpreadsheetMlInternBridgeService
from agent.services.spreadsheet_training_profile_service import SpreadsheetTrainingProfileService
from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy
from ananta_contracts.spreadsheet_studio import canonical_digest


def _dataset() -> dict:
    recipe = {
        "schema": "ananta.spreadsheet-dataset-recipe-manifest.v1",
        "recipe_version": "recipe-v1",
        "task_kinds": ["spreadsheet_actions"],
    }
    recipe["recipe_digest"] = canonical_digest(recipe)
    return {
        "dataset_id": "dataset-one",
        "dataset_digest": "1" * 64,
        "digest": "2" * 64,
        "policy_version": "spreadsheet-learning-policy.v1",
        "recipe_manifest": recipe,
        "split_lock": {"split_lock_digest": "3" * 64},
        "materialization": {"maximum_cells_per_record": 4},
        "split_seed": "split-v1",
        "split_percent": {"train": 70, "validation": 10, "eval": 10, "test": 10},
        "readiness": {"dry_run_ready": True, "training_ready": True},
    }


def _admission(dataset: dict) -> dict:
    value = {
        "admission_id": "admission-one",
        "dataset_id": dataset["dataset_id"],
        "base_model": "local/base",
        "model_digest": "4" * 64,
        "resource_profile_id": "rtx3080-safe",
        "resource_backend": "unsloth",
        "resource_profile_digest": "5" * 64,
        "decision": "go",
    }
    value["admission_digest"] = canonical_digest(value)
    return value


def _profile(dataset: dict, admission: dict) -> dict:
    strategy = SpreadsheetTrainingTaskFamilyStrategy()
    value = {
        "schema": "ananta.spreadsheet-training-profile.v1",
        "profile_id": "spreadsheet-rtx3080-one",
        "profile_version": "spreadsheet-lora-profile.v1",
        "base_model": admission["base_model"],
        "model_digest": admission["model_digest"],
        "backend": admission["resource_backend"],
        "method": "qlora",
        "quantization": "4bit",
        "lora_rank": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
        "max_sequence_length": 2048,
        "max_cells_per_example": 512,
        "seed": 42,
        "max_steps": 100,
        "num_train_epochs": 1.0,
        "learning_rate": 0.0002,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "evaluation_steps": 10,
        "early_stopping_patience": 3,
        "checkpoint_interval_steps": 10,
        "resume_allowed": True,
        "gpu_profile": admission["resource_profile_id"],
        "resource_profile_digest": admission["resource_profile_digest"],
        "dataset_recipe_digest": dataset["recipe_manifest"]["recipe_digest"],
        "split_lock_digest": dataset["split_lock"]["split_lock_digest"],
        "action_schema_digest": strategy.schema_digest,
        "serializer_digest": strategy.serializer_digest,
        "policy_digest": canonical_digest({"policy_version": dataset["policy_version"]}),
    }
    return {**value, "profile_digest": canonical_digest(value)}


def _record() -> dict:
    return {
        "instruction": "Set the governed value",
        "input": json.dumps({"schema": "ananta.spreadsheet-training-context.v1", "cells": []}),
        "output": json.dumps(
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
        ),
        "task_kind": "spreadsheet_actions",
        "privacy_class": "consented_masked",
        "quality_label": "accepted",
        "source_document_version": 1,
        "record_digest": "6" * 64,
        "feedback_id": "feedback-one",
        "consent_id": "consent-one",
        "consent_digest": "7" * 64,
        "lineage_root_id": "document-one",
        "split": "train",
        "recipe_version": "recipe-v1",
    }


def test_profile_is_closed_schema_valid_and_projects_only_worker_configuration() -> None:
    dataset = _dataset()
    admission = _admission(dataset)
    profile = _profile(dataset, admission)

    projection = SpreadsheetTrainingProfileService().project(profile, dataset=dataset, admission=admission)

    assert projection["hyperparameters"] == {
        "lora_rank": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
        "learning_rate": 0.0002,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "max_steps": 100,
        "num_train_epochs": 1.0,
        "max_seq_length": 2048,
        "load_in_4bit": True,
        "evaluation_steps": 10,
        "early_stopping_patience": 3,
        "seed": 42,
    }
    governance = projection["spreadsheet_governance"]
    assert governance["training_profile_digest"] == profile["profile_digest"]
    assert governance["training_admission_digest"] == admission["admission_digest"]
    assert governance["governance_digest"] == canonical_digest(
        {key: value for key, value in governance.items() if key != "governance_digest"}
    )
    schema = json.loads(Path("schemas/spreadsheet-studio/training-profile.v1.json").read_text())
    Draft202012Validator(schema).validate(profile)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("dataset_recipe_digest", "9" * 64, "dataset_recipe_digest_mismatch"),
        ("gpu_profile", "generic-safe", "gpu_profile_mismatch"),
        ("quantization", "none", "quantization_invalid"),
    ],
)
def test_profile_tampering_and_stale_bindings_fail_automatically(field: str, value: object, reason: str) -> None:
    dataset = _dataset()
    admission = _admission(dataset)
    profile = _profile(dataset, admission)
    profile[field] = value
    profile["profile_digest"] = canonical_digest(
        {key: child for key, child in profile.items() if key != "profile_digest"}
    )

    with pytest.raises((PermissionError, ValueError), match=reason):
        SpreadsheetTrainingProfileService().project(profile, dataset=dataset, admission=admission)


def test_profile_digest_and_cell_budget_are_enforced() -> None:
    dataset = _dataset()
    admission = _admission(dataset)
    tampered = _profile(dataset, admission)
    tampered["max_steps"] = 101
    with pytest.raises(ValueError, match="digest_mismatch"):
        SpreadsheetTrainingProfileService().project(tampered, dataset=dataset, admission=admission)

    bounded = _profile(dataset, admission)
    bounded["max_cells_per_example"] = 3
    bounded["profile_digest"] = canonical_digest(
        {key: child for key, child in bounded.items() if key != "profile_digest"}
    )
    with pytest.raises(PermissionError, match="cell_budget_exceeded"):
        SpreadsheetTrainingProfileService().project(bounded, dataset=dataset, admission=admission)


def test_v3_bridge_derives_live_job_without_dataset_content_in_control_metadata(tmp_path: Path) -> None:
    dataset = _dataset()
    admission = _admission(dataset)
    profile = _profile(dataset, admission)
    path = tmp_path / "dataset.jsonl"
    path.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    captured: dict = {}

    class Learning:
        def get_dataset(self, **_kwargs):
            return dict(dataset)

        def dataset_path(self, **_kwargs):
            return path

    class Admissions:
        def require_go(self, **kwargs):
            assert kwargs["admission_id"] == "admission-one"
            assert kwargs["base_model"] == "local/base"
            return dict(admission)

    class Catalog:
        def create_from_records(self, **kwargs):
            assert len(kwargs["records"]) == 1
            return {"dataset_id": "catalog-one", "record_count": 1, "revision": 1}

        def validate_dataset(self, **_kwargs):
            return {"ok": True}

    class Split:
        def split(self, **_kwargs):
            return {"dataset": {"dataset_id": "catalog-one", "record_count": 1, "revision": 2}}

    class RepositoryBridge:
        def sync(self, _principal, _summary, **_kwargs):
            return {"id": "ml-dataset-one"}

    class Control:
        def create_job(self, _principal, command, *, idempotency_key):
            assert idempotency_key == "profile-job-one"
            parsed = CreateTrainingJobCommand.from_mapping(command)
            captured.update(parsed.request_spec)
            return {"id": "job-one", "status": "queued"}, False

    result = SpreadsheetMlInternBridgeService(
        learning=Learning(),
        catalog=Catalog(),
        split=Split(),
        repository_bridge=RepositoryBridge(),
        control=Control(),
        admissions=Admissions(),
    ).start_training(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload={
            "schema": "ananta.spreadsheet-training-command.v3",
            "dataset_id": "dataset-one",
            "mode": "live",
            "admission_id": "admission-one",
            "training_profile": profile,
            "live_confirmed": True,
            "risk_reason": "automatic governed profile training",
        },
        idempotency_key="profile-job-one",
    )

    assert result["schema"] == "ananta.spreadsheet-training-job-admission.v2"
    assert result["training_profile_digest"] == profile["profile_digest"]
    assert captured["resume_allowed"] is True
    assert captured["gpu_profile"] == "rtx3080-safe"
    assert captured["hyperparameters"]["load_in_4bit"] is True
    assert "training_profile" not in captured
    assert "cells" not in json.dumps(captured)
