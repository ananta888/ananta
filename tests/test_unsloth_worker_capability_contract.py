from __future__ import annotations

import pytest

from ananta_contracts.unsloth_capability import (
    UnslothWorkerCapabilityContractError,
    compose_worker_capability_probe,
    progress_telemetry,
    validate_progress_telemetry,
    validate_worker_capability_probe,
    worker_gpu_profile_limits,
)


def _probe() -> dict:
    return compose_worker_capability_probe(
        contract_version="ananta.lora-training.v1",
        resource_profile="nvidia",
        active_gpu_profile="rtx3080-safe",
        backend_availability={
            backend: (True, None)
            for backend in (
                "mock",
                "peft_trl",
                "unsloth",
                "unsloth_vision",
                "unsloth_audio",
                "unsloth_embedding",
            )
        },
        package_versions={"torch": "2.7.0", "unsloth": "2026.7", "unsloth_zoo": "2026.7"},
        hardware={
            "cuda_available": True,
            "torch_version": "2.7.0",
            "cuda_version": "12.8",
            "device_count": 1,
            "device_name": "RTX 3080",
            "total_vram_bytes": 10 * 1024**3,
        },
        runtime_ready=True,
    )


def test_worker_probe_has_closed_versioned_shape_and_package_variants() -> None:
    probe = _probe()

    assert validate_worker_capability_probe(probe) == probe
    assert probe["backends"]["unsloth_vision"]["variant"] == "unsloth_vision"
    assert probe["backends"]["unsloth"]["operations"] == [
        "train_lora",
        "evaluate_lora",
    ]
    assert probe["backends"]["unsloth_vision"]["operations"] == [
        "train_lora"
    ]
    assert probe["packages"]["unsloth_zoo"]["version"] == "2026.7"
    assert probe["limits"]["reserve_bytes"] == 1024**3


def test_worker_probe_rejects_missing_or_incompatible_fields() -> None:
    probe = _probe()
    del probe["hardware"]["cuda_version"]

    with pytest.raises(UnslothWorkerCapabilityContractError):
        validate_worker_capability_probe(probe)


def test_rtx3080_contract_matches_worker_admission_bounds() -> None:
    limits = worker_gpu_profile_limits("rtx3080-safe")

    assert limits["capacity_bytes"] == 10 * 1024**3
    assert limits["reserve_bytes"] == 1024**3
    assert limits["usable_bytes"] == 9 * 1024**3
    assert limits["max_train_batch_size"] == 1
    assert limits["max_sequence_length"] == 2048
    assert limits["required_quantization"] == "4bit"


def test_optional_progress_metrics_are_explicitly_unavailable() -> None:
    telemetry = progress_telemetry({"tokens_per_second": 42.5})

    assert validate_progress_telemetry(telemetry) == telemetry
    assert telemetry["tokens_per_second"]["status"] == "available"
    assert telemetry["gpu_utilization_percent"] == {
        "status": "unavailable",
        "value": None,
        "unit": "percent",
        "reason_code": "metric_unavailable",
    }
