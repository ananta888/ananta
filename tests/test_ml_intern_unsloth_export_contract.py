from __future__ import annotations

import pytest

from agent.services.ml_intern_training_contract import (
    CreateTrainingJobCommand,
    MlInternTrainingContractError,
)
from agent.services.ml_intern_training_worker_port import (
    MlInternTrainingWorkerTransportError,
    _worker_exports,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset_id": "dataset-1",
        "job_type": "train_lora",
        "mode": "dry_run",
        "backend": "unsloth",
        "base_model": "local-model",
        "exports": [{"format": "adapter"}],
    }
    payload.update(overrides)
    return payload


def test_hub_normalizes_adapter_merged_and_gguf_exports() -> None:
    command = CreateTrainingJobCommand.from_mapping(
        _payload(
            allow_merge=True,
            exports=[
                {"format": "ADAPTER"},
                {"format": "merged_16bit"},
                {"format": "GGUF", "quantization_method": "Q5_K_M"},
            ],
        )
    )

    assert command.request_spec["exports"] == [
        {"format": "adapter"},
        {"format": "merged_16bit"},
        {"format": "gguf", "quantization_method": "q5_k_m"},
    ]


def test_hub_requires_explicit_merge_confirmation_for_merged_exports() -> None:
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping(
            _payload(exports=[{"format": "merged_16bit"}])
        )

    assert error.value.reason_code == "merge_confirmation_required"


def test_hub_rejects_exports_for_non_unsloth_backend() -> None:
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping(_payload(backend="peft_trl"))

    assert error.value.reason_code == "unsloth_export_backend_required"


def test_worker_projection_defends_against_path_fields() -> None:
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _worker_exports(
            {
                "allow_merge": True,
                "exports": [{"format": "adapter", "destination": "/tmp/escape"}],
            },
            backend="unsloth",
        )

    assert error.value.reason_code == "unsloth_exports_invalid"
