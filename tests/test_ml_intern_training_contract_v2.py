from __future__ import annotations

import pytest

from agent.services.ml_intern_training_contract import (
    CreateTrainingJobCommand,
    MlInternTrainingContractError,
    assert_job_transition,
    sanitize_event_payload,
)
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


def _payload() -> dict:
    return {
        "dataset_id": "lora-dataset-one",
        "job_type": "train_lora",
        "mode": "dry_run",
        "backend": "mock",
        "base_model": "local-qwen",
        "hyperparameters": {"lora_rank": 8, "max_steps": 2},
    }


def test_create_command_normalizes_bounded_training_request() -> None:
    command = CreateTrainingJobCommand.from_mapping(_payload())
    assert command.dataset_id == "lora-dataset-one"
    assert command.backend == "mock"
    assert command.request_spec["hyperparameters"]["max_steps"] == 2


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("mode", "super-live", "job_mode_invalid"),
        ("backend", "shell", "job_backend_invalid"),
        ("method", "full-finetune", "training_method_invalid"),
        ("dataset_id", "../../secret", "dataset_id_invalid"),
    ],
)
def test_create_command_rejects_invalid_contract_values(field: str, value: object, reason_code: str) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping(payload)
    assert error.value.reason_code == reason_code


def test_create_command_rejects_unknown_fields_and_unsafe_hyperparameters() -> None:
    with pytest.raises(MlInternTrainingContractError, match="unknown job fields"):
        CreateTrainingJobCommand.from_mapping({**_payload(), "worker_url": "http://attacker"})
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping(
            {**_payload(), "hyperparameters": {"batch_size": 999999}}
        )
    assert error.value.reason_code == "hyperparameter_out_of_bounds"


@pytest.mark.parametrize(
    "field",
    ["lora_rank", "lora_alpha", "batch_size", "gradient_accumulation_steps", "max_steps", "max_seq_length"],
)
def test_create_command_rejects_fractional_integer_hyperparameters(field: str) -> None:
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping(
            {**_payload(), "hyperparameters": {field: 1.5}}
        )
    assert error.value.reason_code == "hyperparameter_invalid"


def test_live_command_requires_strict_confirmation_and_string_reason() -> None:
    live = {**_payload(), "mode": "live"}
    with pytest.raises(MlInternTrainingContractError) as missing:
        CreateTrainingJobCommand.from_mapping(live)
    assert missing.value.reason_code == "live_confirmation_required"

    with pytest.raises(MlInternTrainingContractError) as typed:
        CreateTrainingJobCommand.from_mapping(
            {**live, "live_confirmed": True, "risk_reason": {"reason": "not a string"}}
        )
    assert typed.value.reason_code == "live_risk_reason_required"


@pytest.mark.parametrize(
    ("config", "payload", "reason_code"),
    [
        ({"enabled": True}, {"backend": "peft_trl"}, "dry_run_backend_invalid"),
        (
            {"enabled": True, "mode": "dry_run"},
            {"mode": "live", "live_confirmed": True, "risk_reason": "explicit live test"},
            "live_mode_disabled",
        ),
        (
            {"enabled": True, "require_secret_scan": True},
            {"require_secret_scan": False},
            "training_policy_override_denied",
        ),
        (
            {"enabled": True},
            {"gpu_profile": "none", "hyperparameters": {"batch_size": 3}},
            "gpu_profile_batch_size_exceeded",
        ),
    ],
)
def test_hub_admission_rejects_unsafe_mode_policy_and_profile_combinations(
    config: dict, payload: dict, reason_code: str
) -> None:
    service = MlInternTrainingControlService(config)
    with pytest.raises(MlInternTrainingContractError) as error:
        service.create_job(
            # These failures precede repository lookup by design.
            MlInternTrainingPrincipal("tenant", "admin"),
            {**_payload(), **payload},
            idempotency_key="admission-policy-test",
        )
    assert error.value.reason_code == reason_code


def test_create_command_rejects_unknown_gpu_profile() -> None:
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping({**_payload(), "gpu_profile": "unbounded-gpu"})
    assert error.value.reason_code == "gpu_profile_invalid"


def test_terminal_transition_is_rejected() -> None:
    with pytest.raises(MlInternTrainingContractError) as error:
        assert_job_transition("completed", "running")
    assert error.value.status_code == 409


def test_event_payload_is_content_free_and_finite() -> None:
    safe = sanitize_event_payload(
        {
            "phase": "training",
            "current_step": 2,
            "train_loss": 0.3,
            "prompt": "private sample",
            "token": "secret",
            "eval_loss": float("nan"),
        }
    )
    assert safe == {"phase": "training", "current_step": 2, "train_loss": 0.3}


def test_event_payload_rejects_typed_field_injection_and_out_of_bounds_values() -> None:
    safe = sanitize_event_payload(
        {
            "phase": "training\nphase",
            "current_step": "../../secret",
            "max_steps": True,
            "epoch": float("inf"),
            "train_loss": "credential=value",
            "eval_loss": -1,
            "learning_rate": 4.0,
            "progress_percent": 101,
            "checkpoint_ref": "/private/checkpoint",
            "retryable": "yes",
        }
    )

    assert safe == {"phase": "trainingphase"}


def test_hub_result_projections_drop_non_finite_metrics_and_invalid_artifact_sizes() -> None:
    worker_result = {
        "metrics": {
            "finite": 0.25,
            "nan": float("nan"),
            "infinity": float("inf"),
        },
        "artifacts": [
            {"name": "valid.json", "sha256": "a" * 64, "size_bytes": 12},
            {"name": "invalid.json", "sha256": "b" * 64, "size_bytes": float("nan")},
        ],
    }

    persisted = MlInternTrainingControlService._safe_result_summary(worker_result)  # noqa: SLF001
    projected = MlInternTrainingReadModelService._safe_result(persisted)  # noqa: SLF001

    assert persisted["metrics"] == {"finite": 0.25, "nan": None, "infinity": None}
    assert projected["metrics"] == persisted["metrics"]
    assert [artifact["name"] for artifact in projected["artifacts"]] == ["valid.json"]


def test_hub_checkpoint_contract_rejects_unknown_nested_fields() -> None:
    checkpoint = {
        "relative_path": "jobs/job-1/checkpoints/checkpoint-1.json",
        "binding": {
            "job_id": "job-1",
            "source_attempt_id": "attempt-1",
            "base_model_hash": "a" * 64,
            "dataset_hash": "b" * 64,
            "configuration_hash": "c" * 64,
            "checkpoint_sha256": "d" * 64,
            "worker_url": "http://attacker.invalid",
        },
    }

    with pytest.raises(MlInternTrainingContractError) as error:
        MlInternTrainingControlService._normalize_resume_checkpoint(checkpoint)  # noqa: SLF001

    assert error.value.reason_code == "resume_checkpoint_invalid"
