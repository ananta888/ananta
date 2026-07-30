from __future__ import annotations

import pytest

from ananta_contracts.model_intelligence import (
    CAPABILITY_DESCRIPTOR_SCHEMA,
    CapabilityReasonCode,
    CapabilityState,
    ModelIntelligenceContractError,
    parse_model_intelligence_contract,
)


def _conditional_payload() -> dict[str, object]:
    return {
        "extensions": {},
        "schema": CAPABILITY_DESCRIPTOR_SCHEMA,
        "model_id": (
            "model_4f992bdd84a4efa805bf8bebb7c06cbd5755e3406a4b6fe49abcc08907d63ba7"
        ),
        "capability_id": "analysis.static.tensor-statistics",
        "state": "conditional",
        "evidence": "declared",
        "adapter_id": "hf-local-v1",
        "adapter_version": "1.0.0",
        "reason_code": "requires_compatible_model_task",
    }


def test_conditional_capability_preserves_canonical_state_and_reason() -> None:
    parsed = parse_model_intelligence_contract(
        "capability_descriptor",
        _conditional_payload(),
    )

    assert parsed.state is CapabilityState.CONDITIONAL
    assert (
        parsed.reason_code
        is CapabilityReasonCode.REQUIRES_COMPATIBLE_MODEL_TASK
    )


def test_conditional_capability_requires_reason_code() -> None:
    payload = _conditional_payload()
    payload["reason_code"] = None

    with pytest.raises(ModelIntelligenceContractError):
        parse_model_intelligence_contract("capability_descriptor", payload)
