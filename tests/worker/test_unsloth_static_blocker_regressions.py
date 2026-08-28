from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from agent.services.ml_intern_training_worker_port import (
    HttpMlInternTrainingWorkerPort,
    MlInternTrainingWorkerTransportError,
    WORKER_CONTRACT_VERSION,
    _validate_worker_event,
)
from scripts.run_lora_training_smoke import _nvidia_runtime_backend, _support_claim
from worker.training.backends.base import TrainingBackendError
from worker.training.runtime import _safe_resource_admission_payload


def _event(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "contract_version": WORKER_CONTRACT_VERSION,
        "sequence": 1,
        "timestamp": 1.0,
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "type": event_type,
        "payload": payload,
    }


def _resource_admission() -> dict[str, object]:
    return {
        "profile": "nvidia",
        "admitted": True,
        "estimated_peak_bytes": 1024,
        "usable_bytes": 2048,
        "reserve_bytes": 512,
        "assumptions": ["local model metadata"],
        "estimate_only": True,
        "reason_code": "vram_admission_admitted",
    }


def test_worker_port_admits_every_composed_training_backend() -> None:
    default = inspect.signature(HttpMlInternTrainingWorkerPort.__init__).parameters[
        "admitted_backends"
    ].default

    assert set(default) == {
        "mock",
        "needle",
        "peft_trl",
        "unsloth",
        "unsloth_vision",
        "unsloth_audio",
        "unsloth_embedding",
    }


def test_closed_worker_event_contract_accepts_only_declared_unsloth_fields() -> None:
    _validate_worker_event(_event("phase", {"phase": "loading_model", "modality": "vision"}))
    _validate_worker_event(_event("resource_admission", _resource_admission()))

    with pytest.raises(MlInternTrainingWorkerTransportError, match="modality"):
        _validate_worker_event(_event("phase", {"phase": "loading_model", "modality": "image"}))
    invalid = _resource_admission()
    invalid["device_name"] = "secret"
    with pytest.raises(MlInternTrainingWorkerTransportError, match="unknown fields"):
        _validate_worker_event(_event("resource_admission", invalid))


def test_runtime_resource_admission_contract_is_exact_and_copies_assumptions() -> None:
    payload = _resource_admission()

    clean = _safe_resource_admission_payload(payload)

    assert clean == payload
    assert clean["assumptions"] is not payload["assumptions"]
    missing = _resource_admission()
    missing.pop("reserve_bytes")
    with pytest.raises(TrainingBackendError, match="exactly"):
        _safe_resource_admission_payload(missing)


def test_documented_event_schema_matches_runtime_extensions() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs/contracts/mlintern-lora-training-event.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert "resource_admission" in schema["properties"]["type"]["enum"]
    assert schema["$defs"]["phasePayload"]["properties"]["modality"]["enum"] == [
        "text",
        "vision",
        "audio",
        "embedding",
    ]
    assert set(schema["$defs"]["resourceAdmissionPayload"]["required"]) == set(
        _resource_admission()
    )


def test_nvidia_smoke_selects_unsloth_without_claiming_unrun_platform_stages() -> None:
    assert _nvidia_runtime_backend("unsloth").name == "unsloth"
    claim = _support_claim(
        backend="unsloth",
        nvidia_result={
            "status": "passed",
            "platform_stage_coverage": {
                "training": {"status": "passed"},
                "export": {"status": "passed"},
                "training_evaluation": {"status": "passed"},
                "adapter_evaluation": {"status": "not_run"},
                "promotion": {"status": "not_run"},
                "runtime_load": {"status": "not_run"},
            },
        },
        evidence_ids={
            "complete": True,
            "src_ids": ["SRC_release"],
            "run_ids": ["RUN_gpu"],
        },
        image_attestation={"runtime_image_digest_supplied": True},
        versions={"packages": {"unsloth": "test-version"}},
    )

    assert claim["verified"] is False
    assert "unsloth_adapter_evaluation_not_verified" in claim["reason_codes"]
    assert "unsloth_promotion_not_verified" in claim["reason_codes"]
    assert "unsloth_runtime_load_not_verified" in claim["reason_codes"]
