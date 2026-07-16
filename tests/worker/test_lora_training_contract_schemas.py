from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

CONTRACT_ROOT = Path(__file__).parents[2] / "docs" / "contracts"
SCHEMA_NAMES = (
    "mlintern-lora-training-request.v1.schema.json",
    "mlintern-lora-training-status.v1.schema.json",
    "mlintern-lora-training-event.v1.schema.json",
    "mlintern-lora-training-result.v1.schema.json",
    "mlintern-lora-training-artifact.v1.schema.json",
)


def _schemas() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8")) for name in SCHEMA_NAMES)


def _registry() -> Registry:
    registry = Registry()
    for schema in _schemas():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_registry())


def _artifact() -> dict[str, Any]:
    return {
        "name": "adapter_model.safetensors",
        "sha256": "a" * 64,
        "size_bytes": 128,
        "media_type": "application/octet-stream",
    }


def _request() -> dict[str, Any]:
    split = {"relative_path": "dataset/train.jsonl", "sha256": "b" * 64, "record_count": 2}
    return {
        "contract_version": "ananta.lora-training.v1",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "job_type": "train_lora",
        "backend": "mock",
        "resource_profile": "mock",
        "tenant_scope_digest": "c" * 64,
        "workspace_ref": "jobs/job-1",
        "deadline_epoch_ms": 1_900_000_000_000,
        "base_model": {
            "model_id": "local/base",
            "relative_path": "models/base",
            "snapshot_hash": "d" * 64,
        },
        "dataset": {
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "train": split,
            "validation": {**split, "relative_path": "dataset/validation.jsonl"},
        },
        "configuration": {
            "seed": 42,
            "max_steps": 10,
            "num_train_epochs": 1.0,
            "learning_rate": 0.0002,
            "train_batch_size": 1,
            "eval_batch_size": 1,
            "gradient_accumulation_steps": 1,
            "eval_steps": 5,
            "save_steps": 5,
            "early_stopping_patience": 2,
            "lora_rank": 8,
            "lora_alpha": 16,
            "lora_dropout": 0.05,
            "max_sequence_length": 256,
            "quantization": "none",
            "gradient_checkpointing": True,
            "target_modules": ["q_proj", "v_proj"],
        },
    }


def _status(status: str = "succeeded") -> dict[str, Any]:
    artifacts = [_artifact()] if status == "succeeded" else []
    error = {"code": "out_of_memory", "message": "bounded failure", "retryable": True} if status == "failed" else None
    return {
        "contract_version": "ananta.lora-training.v1",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "job_type": "train_lora",
        "backend": "mock",
        "status": status,
        "created_at": 1.0,
        "updated_at": 2.0,
        "heartbeat_at": 2.0,
        "progress": {"step": 10, "max_steps": 10, "loss": 0.1},
        "metrics": {"adapter": {"eval_loss": 0.1}},
        "artifacts": artifacts,
        "resume_checkpoint": None,
        "cancel_mode": "graceful" if status == "cancelled" else None,
        "error": error,
    }


def _event() -> dict[str, Any]:
    return {
        "contract_version": "ananta.lora-training.v1",
        "sequence": 1,
        "timestamp": 2.0,
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "type": "progress",
        "payload": {"step": 1, "max_steps": 10, "loss": 0.5},
    }


def test_all_lora_worker_contract_schemas_are_valid_draft_2020_12() -> None:
    for schema in _schemas():
        Draft202012Validator.check_schema(schema)
        assert ".v1.schema.json" in schema["$id"]


@pytest.mark.parametrize(
    ("schema_name", "payload"),
    [
        (SCHEMA_NAMES[0], _request()),
        (SCHEMA_NAMES[1], _status()),
        (SCHEMA_NAMES[2], _event()),
        (SCHEMA_NAMES[3], _status()),
        (SCHEMA_NAMES[4], _artifact()),
    ],
)
def test_versioned_schemas_accept_current_worker_envelopes(schema_name: str, payload: dict[str, Any]) -> None:
    _validator(schema_name).validate(payload)


@pytest.mark.parametrize(
    ("schema_name", "payload"),
    [
        (SCHEMA_NAMES[0], {**_request(), "delegate_to": "other-worker"}),
        (SCHEMA_NAMES[1], {key: value for key, value in _status().items() if key != "correlation_id"}),
        (SCHEMA_NAMES[2], {**_event(), "payload": {"step": 1, "max_steps": 10, "prompt": "private"}}),
        (SCHEMA_NAMES[3], {**_status("cancelled"), "cancel_mode": "kill-anything"}),
        (SCHEMA_NAMES[4], {**_artifact(), "absolute_path": "/private/adapter"}),
    ],
)
def test_versioned_schemas_reject_orchestration_unknown_and_uncorrelated_fields(
    schema_name: str,
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _validator(schema_name).validate(payload)


def test_request_schema_closes_nested_objects() -> None:
    request = copy.deepcopy(_request())
    request["dataset"]["train"]["source_path"] = "/private/train.jsonl"

    with pytest.raises(ValidationError):
        _validator(SCHEMA_NAMES[0]).validate(request)


@pytest.mark.parametrize("unsafe_ref", ["a/../b", "a//b"])
def test_schema_path_rules_match_runtime_traversal_and_separator_rejection(unsafe_ref: str) -> None:
    request = _request()
    request["workspace_ref"] = unsafe_ref
    artifact = {**_artifact(), "name": unsafe_ref}

    with pytest.raises(ValidationError):
        _validator(SCHEMA_NAMES[0]).validate(request)
    with pytest.raises(ValidationError):
        _validator(SCHEMA_NAMES[4]).validate(artifact)


def test_legacy_path_based_job_schema_is_explicitly_deprecated_without_shape_change() -> None:
    schema = json.loads((CONTRACT_ROOT / "mlintern-training-job.schema.json").read_text(encoding="utf-8"))
    assert "deprecated" in schema["title"].lower()
    assert schema["$id"] == "mlintern-training-job.schema.json"
    assert "dataset_path" in schema["properties"]
