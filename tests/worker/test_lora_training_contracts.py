from __future__ import annotations

import copy

import pytest

from worker.training.contracts import (
    CONTRACT_VERSION,
    TrainingContractError,
    canonical_sha256,
    parse_job_request,
)


def _split(name: str) -> dict[str, object]:
    return {"relative_path": f"{name}.jsonl", "sha256": "a" * 64, "record_count": 1}


def _training_request() -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "job_type": "train_lora",
        "backend": "mock",
        "resource_profile": "mock",
        "tenant_scope_digest": "b" * 64,
        "workspace_ref": "jobs/job-1",
        "deadline_epoch_ms": 1_900_000_000_000,
        "base_model": {
            "model_id": "local/base",
            "relative_path": "base",
            "snapshot_hash": "c" * 64,
        },
        "dataset": {
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "train": _split("train"),
            "validation": _split("validation"),
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
        "resume_checkpoint": {
            "relative_path": "jobs/job-0/checkpoints/checkpoint-1.json",
            "binding": {
                "job_id": "job-0",
                "source_attempt_id": "attempt-0",
                "base_model_hash": "c" * 64,
                "dataset_hash": "d" * 64,
                "configuration_hash": "e" * 64,
                "checkpoint_sha256": "f" * 64,
            },
        },
    }


def _governance() -> dict[str, str]:
    bindings = {
        "training_profile_digest": "1" * 64,
        "base_model_digest": "c" * 64,
        "dataset_manifest_digest": "2" * 64,
        "dataset_artifact_digest": "3" * 64,
        "dataset_recipe_digest": "4" * 64,
        "split_lock_digest": "5" * 64,
        "action_schema_digest": "6" * 64,
        "serializer_digest": "7" * 64,
        "policy_digest": "8" * 64,
        "resource_profile_digest": "9" * 64,
        "training_admission_digest": "a" * 64,
    }
    return {**bindings, "governance_digest": canonical_sha256(bindings)}


def _evaluation_request() -> dict[str, object]:
    request = _training_request()
    request["job_type"] = "evaluate_existing_adapter"
    request.pop("dataset")
    request.pop("resume_checkpoint")
    request["adapter"] = {
        "adapter_id": "adapter-1",
        "relative_path": "adapter",
        "sha256": "d" * 64,
    }
    request["validation_dataset"] = {
        "dataset_id": "dataset-1",
        "dataset_version": "v1",
        "validation": _split("validation"),
    }
    request["configuration"] = {
        "seed": 42,
        "batch_size": 1,
        "max_sequence_length": 256,
        "max_samples": 100,
        "quantization": "none",
        "scorer_name": "generic",
    }
    return request


@pytest.mark.parametrize(
    "path",
    [
        ("base_model",),
        ("dataset",),
        ("dataset", "train"),
        ("dataset", "validation"),
        ("configuration",),
        ("resume_checkpoint",),
        ("resume_checkpoint", "binding"),
    ],
)
def test_training_request_rejects_unknown_fields_at_every_nested_boundary(path: tuple[str, ...]) -> None:
    request = copy.deepcopy(_training_request())
    target: dict[str, object] = request
    for segment in path:
        child = target[segment]
        assert isinstance(child, dict)
        target = child
    target["worker_url"] = "http://attacker.invalid"

    with pytest.raises(TrainingContractError) as error:
        parse_job_request(request)

    assert error.value.code == "invalid_contract_shape"


@pytest.mark.parametrize(
    "path",
    [
        ("adapter",),
        ("validation_dataset",),
        ("validation_dataset", "validation"),
        ("configuration",),
    ],
)
def test_evaluation_request_rejects_unknown_fields_at_every_nested_boundary(path: tuple[str, ...]) -> None:
    request = copy.deepcopy(_evaluation_request())
    target: dict[str, object] = request
    for segment in path:
        child = target[segment]
        assert isinstance(child, dict)
        target = child
    target["delegate_to"] = "another-worker"

    with pytest.raises(TrainingContractError) as error:
        parse_job_request(request)

    assert error.value.code == "invalid_contract_shape"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_contract_numbers_and_canonical_hash_reject_non_finite_values(value: float) -> None:
    request = _training_request()
    configuration = request["configuration"]
    assert isinstance(configuration, dict)
    configuration["learning_rate"] = value

    with pytest.raises(TrainingContractError, match="between"):
        parse_job_request(request)
    with pytest.raises(TrainingContractError, match="non-finite"):
        canonical_sha256({"metric": value})


def test_integer_fields_reject_float_and_string_coercion() -> None:
    for value in (1.5, "1"):
        request = _training_request()
        configuration = request["configuration"]
        assert isinstance(configuration, dict)
        configuration["max_steps"] = value
        with pytest.raises(TrainingContractError, match="must be an integer"):
            parse_job_request(request)


def test_text_and_hash_fields_reject_type_or_case_coercion() -> None:
    numeric_id = _training_request()
    numeric_id["job_id"] = 1
    with pytest.raises(TrainingContractError, match="must be a string"):
        parse_job_request(numeric_id)

    uppercase_hash = _training_request()
    base_model = uppercase_hash["base_model"]
    assert isinstance(base_model, dict)
    base_model["snapshot_hash"] = "A" * 64
    with pytest.raises(TrainingContractError, match="lowercase"):
        parse_job_request(uppercase_hash)


def test_canonical_hash_rejects_non_json_keys_and_values() -> None:
    with pytest.raises(TrainingContractError, match="non-string field"):
        canonical_sha256({1: "ambiguous"})
    with pytest.raises(TrainingContractError, match="non-JSON value"):
        canonical_sha256({"modules": {"q_proj"}})


def test_training_governance_is_closed_and_digest_bound() -> None:
    request = _training_request()
    request["governance"] = _governance()

    parsed = parse_job_request(request)
    assert parsed.to_dict()["governance"]["training_admission_digest"] == "a" * 64

    tampered = copy.deepcopy(request)
    governance = tampered["governance"]
    assert isinstance(governance, dict)
    governance["dataset_artifact_digest"] = "f" * 64
    with pytest.raises(TrainingContractError) as mismatch:
        parse_job_request(tampered)
    assert mismatch.value.code == "governance_binding_mismatch"

    injected = copy.deepcopy(request)
    governance = injected["governance"]
    assert isinstance(governance, dict)
    governance["dataset_content"] = "private cells"
    with pytest.raises(TrainingContractError) as shape:
        parse_job_request(injected)
    assert shape.value.code == "invalid_contract_shape"

    wrong_model = copy.deepcopy(request)
    governance = wrong_model["governance"]
    assert isinstance(governance, dict)
    governance["base_model_digest"] = "d" * 64
    bindings = {key: value for key, value in governance.items() if key != "governance_digest"}
    governance["governance_digest"] = canonical_sha256(bindings)
    with pytest.raises(TrainingContractError) as model_mismatch:
        parse_job_request(wrong_model)
    assert model_mismatch.value.code == "governance_model_mismatch"
