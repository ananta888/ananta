import pytest
from pydantic import ValidationError

from ananta_contracts.local_model_runtime import (
    LocalRuntimeResourceUsage,
    LocalRuntimeStatus,
    RuntimeHealth,
    RuntimeReadiness,
)


def _status(**overrides):
    values = {
        "snapshot_revision": 1,
        "runtime_id": "needle",
        "provider_id": "needle_sidecar",
        "model_id": "needle-2-45m",
        "execution_device": "cpu",
        "health": RuntimeHealth.HEALTHY,
        "readiness": RuntimeReadiness.READY,
        "reason_code": "runtime_ready",
        "effective_context": 256,
        "context_capacity": 256,
        "capabilities": ("tool_selection",),
        "resources": LocalRuntimeResourceUsage(ram_budget_bytes=1024),
        "timeout_supported": True,
        "cancellation_supported": False,
        "candidate_only": True,
    }
    values.update(overrides)
    return LocalRuntimeStatus(**values)


def test_needle_contract_is_closed_candidate_only_and_content_free():
    status = _status()
    wire = status.model_dump(mode="json", by_alias=True)

    assert wire["schema"] == "ananta.local-model-runtime-status.v1"
    assert wire["orchestration_authority"] is False
    assert "prompt" not in wire
    with pytest.raises(ValidationError):
        _status(candidate_only=False)
    with pytest.raises(ValidationError):
        _status(prompt="secret")


def test_resource_usage_rejects_undeclared_budget_overrun():
    with pytest.raises(ValidationError, match="vram_budget_exceeded"):
        LocalRuntimeResourceUsage(vram_used_bytes=2, vram_budget_bytes=1)
