from __future__ import annotations

from agent.services.audit_event_schema import (
    CANONICAL_AUDIT_EVENT_SCHEMA,
    build_canonical_audit_event,
    classify_context_classes,
)


def test_canonical_schema_required_fields_present() -> None:
    assert CANONICAL_AUDIT_EVENT_SCHEMA["schema"] == "canonical_audit_event.v1"
    for field in ("trace_id", "task_id", "actor", "role", "policy_version", "operation_type", "target", "outcome", "timestamp"):
        assert field in CANONICAL_AUDIT_EVENT_SCHEMA["required_fields"]


def test_context_classification_merges_unique_values() -> None:
    classes = classify_context_classes(
        details={"context_classes": ["repo", "artifact", "repo"]},
        task_metadata={"context_classes": ["wiki"]},
    )
    assert classes == ["repo", "artifact", "wiki"]


def test_build_canonical_event_contains_chain_and_target() -> None:
    event = build_canonical_audit_event(
        trace_id="tr-1",
        task_id="t-1",
        actor="hub",
        role="orchestrator",
        policy_version="v1",
        operation_type="tool_call",
        target={"tool_name": "mcp.read"},
        outcome="success",
        parent_trace_id="parent-1",
        context_classes=["repo"],
        prompt_bundle_class="coding",
        details={"x": 1},
    )
    assert event["schema"] == "canonical_audit_event.v1"
    assert event["target"]["tool_name"] == "mcp.read"
    assert event["chain"]["parent_trace_id"] == "parent-1"
