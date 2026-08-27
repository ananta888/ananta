from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.training_backend import (
    TRAINING_BACKEND_CAPABILITY_VERSION,
    TrainingBackendCapability,
    TrainingBackendContractError,
)


def _capability() -> dict[str, object]:
    return {
        "schema_version": TRAINING_BACKEND_CAPABILITY_VERSION,
        "backend_id": "axolotl",
        "backend_version": "0.18.0",
        "available": True,
        "reason_code": "ok",
        "maturity": "experimental",
        "maintenance": "active",
        "license_spdx": "Apache-2.0",
        "modalities": ["text"],
        "objectives": ["sft"],
        "methods": ["lora", "qlora"],
        "precisions": ["bf16", "fp16"],
        "quantizations": ["4bit", "none"],
        "distributed_modes": ["single_device"],
        "exports": ["adapter"],
        "resume": True,
        "evaluation": True,
        "resource_profiles": ["generic-safe", "rtx3080-safe"],
    }


def test_capability_is_closed_and_matches_json_schema() -> None:
    payload = _capability()
    capability = TrainingBackendCapability.from_mapping(payload)
    schema = json.loads(Path("docs/contracts/training-backend-capability.v3.schema.json").read_text())
    assert list(Draft202012Validator(schema).iter_errors(capability.to_dict())) == []
    assert capability.backend_id == "axolotl"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backend_version", "latest version"),
        ("methods", ["shell"]),
        ("quantizations", ["2bit"]),
        ("exports", ["pickle"]),
    ],
)
def test_capability_rejects_unknown_version_or_features(field: str, value: object) -> None:
    payload = _capability()
    payload[field] = value
    with pytest.raises(TrainingBackendContractError):
        TrainingBackendCapability.from_mapping(payload)


def test_capability_rejects_unknown_fields_and_undeclared_requests() -> None:
    payload = copy.deepcopy(_capability())
    payload["python_import"] = "attacker.module"
    with pytest.raises(TrainingBackendContractError, match="unknown fields"):
        TrainingBackendCapability.from_mapping(payload)

    capability = TrainingBackendCapability.from_mapping(_capability())
    with pytest.raises(TrainingBackendContractError) as error:
        capability.require(
            modality="text",
            objective="dpo",
            method="lora",
            quantization="4bit",
            export="adapter",
        )
    assert error.value.reason_code == "capability_not_declared"


def test_unavailable_capability_requires_non_ok_reason() -> None:
    payload = _capability()
    payload["available"] = False
    with pytest.raises(TrainingBackendContractError, match="disagree"):
        TrainingBackendCapability.from_mapping(payload)
