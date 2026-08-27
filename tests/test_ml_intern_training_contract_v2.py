from __future__ import annotations

import pytest

from agent.services.ml_intern_training_contract import (
    CreateTrainingJobCommand,
    MlInternTrainingContractError,
    UnslothCapabilityFacet,
    UnslothCapabilitySnapshot,
    assert_job_transition,
    normalize_run_ids,
    normalize_source_ids,
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
    assert "source_ids" not in command.request_spec
    assert "run_ids" not in command.request_spec


def test_local_release_target_is_closed_and_backend_bound() -> None:
    command = CreateTrainingJobCommand.from_mapping(
        {
            **_payload(),
            "backend": "needle",
            "method": "lora",
            "release_target": "needle2",
        }
    )
    assert command.request_spec["release_target"] == "needle2"

    for target, backend, reason_code in (
        ("unknown", "needle", "local_adapter_release_target_invalid"),
        ("needle2", "peft_trl", "local_adapter_release_target_backend_mismatch"),
        ("lfm2.5-2.6b-agentic", "needle", "local_adapter_release_target_backend_mismatch"),
    ):
        with pytest.raises(MlInternTrainingContractError) as error:
            CreateTrainingJobCommand.from_mapping({**_payload(), "backend": backend, "release_target": target})
        assert error.value.reason_code == reason_code

    with pytest.raises(MlInternTrainingContractError) as missing:
        CreateTrainingJobCommand.from_mapping({**_payload(), "backend": "needle", "method": "lora"})
    assert missing.value.reason_code == "needle_release_target_required"

    with pytest.raises(MlInternTrainingContractError) as method:
        CreateTrainingJobCommand.from_mapping({**_payload(), "backend": "needle", "release_target": "needle2"})
    assert method.value.reason_code == "needle_training_method_invalid"


@pytest.mark.parametrize("backend", ["unsloth_vision", "unsloth_audio", "unsloth_embedding"])
def test_optional_unsloth_backends_are_additive_contract_values(backend: str) -> None:
    command = CreateTrainingJobCommand.from_mapping({**_payload(), "backend": backend})
    assert command.backend == backend


def test_source_and_run_ids_are_only_normalized_when_provided() -> None:
    assert normalize_source_ids(["SRC_repo:7"]) == ("SRC_repo:7",)
    assert normalize_run_ids(["RUN_training:9"]) == ("RUN_training:9",)
    for field, value, reason_code in (
        ("source_ids", ["invented"], "source_ids_invalid"),
        ("run_ids", ["SRC_wrong-prefix"], "run_ids_invalid"),
    ):
        with pytest.raises(MlInternTrainingContractError) as error:
            CreateTrainingJobCommand.from_mapping({**_payload(), field: value})
        assert error.value.reason_code == reason_code


def test_composed_unsloth_capability_snapshot_is_deterministic_and_sourced() -> None:
    facet = UnslothCapabilityFacet(
        facet_id="training.text",
        available=False,
        reason_code="worker_capability_unavailable",
        source="worker_probe",
        operations=("train_lora",),
        model_kinds=("text",),
    )
    first = UnslothCapabilitySnapshot(
        operating_mode="core_worker",
        detected_variant="core_worker",
        facets=(facet,),
    ).to_mapping()
    second = UnslothCapabilitySnapshot(
        operating_mode="core_worker",
        detected_variant="core_worker",
        facets=(facet,),
    ).to_mapping()
    assert first == second
    assert first["facets"][0]["source"] == "worker_probe"
    assert len(first["snapshot_id"]) == 64


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
        CreateTrainingJobCommand.from_mapping({**_payload(), "hyperparameters": {"batch_size": 999999}})
    assert error.value.reason_code == "hyperparameter_out_of_bounds"


@pytest.mark.parametrize(
    "field",
    ["lora_rank", "lora_alpha", "batch_size", "gradient_accumulation_steps", "max_steps", "max_seq_length"],
)
def test_create_command_rejects_fractional_integer_hyperparameters(field: str) -> None:
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping({**_payload(), "hyperparameters": {field: 1.5}})
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
        (
            {"enabled": True},
            {
                "backend": "needle",
                "method": "lora",
                "release_target": "needle2",
                "gpu_profile": "rtx3080-safe",
            },
            "needle_cpu_profile_required",
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


def test_hub_policy_caps_needle_sequence_before_worker_dispatch() -> None:
    command = CreateTrainingJobCommand.from_mapping(
        {
            **_payload(),
            "backend": "needle",
            "method": "lora",
            "release_target": "needle2",
            "gpu_profile": "none",
            "hyperparameters": {"max_seq_length": 512, "batch_size": 1},
        }
    )
    service = MlInternTrainingControlService({"enabled": True})

    with pytest.raises(MlInternTrainingContractError) as error:
        service._assert_request_policy(command, "none")
    assert error.value.reason_code == "needle_sequence_length_exceeded"


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


def test_event_payload_admits_bounded_resource_metrics_without_content() -> None:
    safe = sanitize_event_payload(
        {
            "tokens_per_second": 17.5,
            "gpu_utilization_percent": 82.0,
            "vram_allocated_bytes": 1024,
            "vram_peak_bytes": 2048,
            "prompt": "must never pass",
        }
    )
    assert safe == {
        "vram_allocated_bytes": 1024,
        "vram_peak_bytes": 2048,
        "tokens_per_second": 17.5,
        "gpu_utilization_percent": 82.0,
    }


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
