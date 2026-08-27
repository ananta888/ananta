from __future__ import annotations

import pytest

from agent.services.ml_intern_backend_selection_service import (
    BackendSelectionError,
    BackendSelectionRequest,
    MlInternBackendSelectionService,
)


def _backends() -> list[dict[str, object]]:
    return [
        {"id": "unsloth", "available": True, "reason_code": None},
        {"id": "peft_trl", "available": True, "reason_code": None},
        {"id": "axolotl", "available": True, "reason_code": None},
        {"id": "llamafactory", "available": False, "reason_code": "dependency_unavailable"},
        {"id": "autotrain", "available": True, "reason_code": None},
        {"id": "torchtune", "available": True, "reason_code": None},
    ]


def test_rtx3080_recommendation_prefers_measured_existing_backend_and_explains() -> None:
    request = BackendSelectionRequest.from_mapping(
        {
            "objective": "sft",
            "method": "qlora",
            "modality": "text",
            "resource_profile": "rtx3080-safe",
            "estimated_model_bytes": 4 * 1024**3,
            "runtime_budget_seconds": 7200,
            "export_format": "adapter",
        }
    )
    result = MlInternBackendSelectionService().recommend(request, backends=_backends())
    assert result["backend"] == "unsloth"
    assert result["requires_confirmation"] is True
    assert result["estimated_resources"]["estimate_only"] is True
    assert result["fallback_policy"] == "new_visible_attempt_only"
    assert {item["backend"] for item in result["alternatives"]} >= {"axolotl", "peft_trl"}


def test_manual_backend_is_preserved_but_never_auto_executes() -> None:
    request = BackendSelectionRequest.from_mapping(
        {
            "manual_backend": "axolotl",
            "resource_profile": "generic-safe",
            "runtime_budget_seconds": 3600,
        }
    )
    result = MlInternBackendSelectionService().recommend(request, backends=_backends())
    assert result["backend"] == "axolotl"
    assert result["mode"] == "manual"
    assert result["requires_confirmation"] is True


def test_unavailable_manual_backend_and_cpu_oom_profile_fail_closed() -> None:
    unavailable = BackendSelectionRequest.from_mapping(
        {"manual_backend": "llamafactory", "resource_profile": "rtx3080-safe"}
    )
    with pytest.raises(BackendSelectionError) as error:
        MlInternBackendSelectionService().recommend(unavailable, backends=_backends())
    assert error.value.reason_code == "manual_backend_unavailable"

    cpu = BackendSelectionRequest.from_mapping({"resource_profile": "cpu", "method": "qlora"})
    with pytest.raises(BackendSelectionError) as error:
        MlInternBackendSelectionService().recommend(cpu, backends=[{"id": "axolotl", "available": True}])
    assert error.value.reason_code == "backend_unavailable"


def test_selection_rejects_unknown_fields_and_marketing_only_backend() -> None:
    with pytest.raises(BackendSelectionError):
        BackendSelectionRequest.from_mapping({"benchmark_claim": "fastest"})
    request = BackendSelectionRequest.from_mapping({})
    with pytest.raises(BackendSelectionError) as error:
        MlInternBackendSelectionService().recommend(
            request,
            backends=[
                {"id": "autotrain", "available": True},
                {"id": "torchtune", "available": True},
            ],
        )
    assert error.value.reason_code == "backend_unavailable"
