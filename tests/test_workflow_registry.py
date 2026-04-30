from __future__ import annotations

from agent.services.workflow_registry import WorkflowRegistry


def test_workflow_registry_loads_and_sorts() -> None:
    reg = WorkflowRegistry(descriptors=[
        {"provider": "mock", "workflow_id": "b", "display_name": "B", "capability": "read", "risk_class": "low", "approval_required": False, "input_schema": {}, "output_schema": {}},
        {"provider": "mock", "workflow_id": "a", "display_name": "A", "capability": "read", "risk_class": "low", "approval_required": False, "input_schema": {}, "output_schema": {}},
    ])
    ids = [d.workflow_id for d in reg.list()]
    assert ids == ["a", "b"]


def test_workflow_registry_unknown_returns_none() -> None:
    reg = WorkflowRegistry(descriptors=[])
    assert reg.get("missing") is None
