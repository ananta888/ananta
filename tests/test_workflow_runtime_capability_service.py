from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent.services.workflow_runtime_capability_service import (
    RuntimeCapabilityDescriptor,
    WorkflowRuntimeCapabilityService,
    default_workflow_runtime_capability_service,
)
from agent.services.workflow_runtime_selection_service import (
    InMemoryRuntimeHealthService,
)


def test_versioned_matrix_contains_native_langgraph_and_temporal() -> None:
    service = default_workflow_runtime_capability_service()

    projection = service.hub_projection()
    by_id = {item["runtime_id"]: item for item in projection["runtimes"]}

    assert projection["schema"] == "ananta.workflow_runtime_capability_matrix.v1"
    assert projection["matrix_version"] == "1.0.0"
    assert set(by_id) == {"ananta-native", "langgraph", "temporal"}
    assert by_id["ananta-native"]["mode"] == "live"
    assert by_id["langgraph"]["restrictions"]
    assert by_id["temporal"]["mode"] == "durable"
    assert by_id["temporal"]["health"]["status"] == "unavailable"
    assert by_id["temporal"]["declared_health"]["reason_code"] == (
        "runtime_health_not_observed"
    )


def test_observed_health_overrides_declaration_for_all_surface_consumers() -> None:
    service = default_workflow_runtime_capability_service(
        health=InMemoryRuntimeHealthService(
            {
                "ananta-native": "ready",
                "langgraph": "degraded",
                "temporal": "unavailable",
            }
        )
    )

    projection = service.hub_projection()
    by_id = {item["runtime_id"]: item for item in projection["runtimes"]}

    assert by_id["ananta-native"]["selection"]["state"] == "compatible"
    assert by_id["langgraph"]["selection"]["state"] == "degraded"
    assert by_id["temporal"]["selection"]["state"] == "blocked"


def test_hub_projection_reports_compatible_and_incompatible_reason_codes() -> None:
    service = default_workflow_runtime_capability_service(
        health=InMemoryRuntimeHealthService(
            {
                "ananta-native": "ready",
                "langgraph": "ready",
                "temporal": "ready",
            }
        )
    )
    projection = service.hub_projection(
        required_capabilities=("durability", "resume")
    )
    by_id = {item["runtime_id"]: item for item in projection["runtimes"]}

    assert by_id["temporal"]["selection"] == {
        "state": "compatible",
        "reason_code": "runtime_capabilities_satisfied",
        "missing_capabilities": [],
    }
    assert by_id["ananta-native"]["selection"] == {
        "state": "incompatible",
        "reason_code": "runtime_capabilities_missing",
        "missing_capabilities": ["durability"],
    }


@pytest.mark.parametrize(
    ("health_status", "expected_state"),
    [
        ("degraded", "degraded"),
        ("unavailable", "blocked"),
        ("disabled", "blocked"),
    ],
)
def test_projection_preserves_non_ready_health(
    health_status: str,
    expected_state: str,
) -> None:
    descriptor = RuntimeCapabilityDescriptor.from_mapping(
        {
            "runtime_id": "ananta-native",
            "runtime_version": "1.0.0",
            "contract_version": "ananta.execution_plan.v1",
            "mode": "live",
            "capabilities": ["retrieval"],
            "restrictions": ["test-only"],
            "health": {
                "status": health_status,
                "reason_code": f"runtime_health_{health_status}",
            },
            "data_localities": ["local"],
            "policy_versions": ["*"],
        }
    )
    # The service requires the three production families, so test the descriptor
    # projection directly for injected live health states.
    projected = descriptor.project(required_capabilities=frozenset({"retrieval"}))

    assert projected["selection"]["state"] == expected_state
    assert projected["selection"]["reason_code"] == f"runtime_health_{health_status}"


def test_missing_required_runtime_fails_matrix_loading() -> None:
    descriptor = RuntimeCapabilityDescriptor.from_mapping(
        {
            "runtime_id": "ananta-native",
            "runtime_version": "1.0.0",
            "contract_version": "ananta.execution_plan.v1",
            "mode": "live",
            "capabilities": ["retrieval"],
            "restrictions": ["test-only"],
            "health": {
                "status": "ready",
                "reason_code": "runtime_health_ready",
            },
            "data_localities": ["local"],
            "policy_versions": ["*"],
        }
    )

    with pytest.raises(ValueError, match="required_runtime_missing"):
        WorkflowRuntimeCapabilityService(
            matrix_version="1.0.0",
            descriptors=(descriptor,),
        )


def test_hub_surface_layer_does_not_import_runtime_implementations() -> None:
    root = Path(__file__).resolve().parents[1]
    surface_paths = (
        root / "agent/services/workflow_runtime_capability_service.py",
        root / "agent/services/workflow_runtime_selection_service.py",
        root / "agent/services/workflow_control_service.py",
        root / "agent/routes/workflow_runtime_operations.py",
        root / "agent/cli/workflow_stream_client.py",
        root / "agent/tui/workflow_stream_client.py",
    )
    forbidden = ("worker", "langgraph", "temporalio")

    for path in surface_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        assert not any(
            module == prefix or module.startswith(prefix + ".")
            for module in imports
            for prefix in forbidden
        ), f"{path} imports a concrete runtime: {imports}"
