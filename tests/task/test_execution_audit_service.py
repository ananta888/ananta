from __future__ import annotations

from agent.services.execution_audit_service import get_execution_audit_service


def test_execution_audit_schema_has_required_fields() -> None:
    schema = get_execution_audit_service().schema()
    assert schema["schema"] == "execution_audit_event.v2"
    assert "trace_id" in schema["required_fields"]
    assert "operation_type" in schema["required_fields"]
    assert "target" in schema["required_fields"]


def test_execution_audit_emit_calls_log_audit(monkeypatch) -> None:
    captured = {}

    def _fake_log(event_type, details):
        captured["event_type"] = event_type
        captured["details"] = details

    monkeypatch.setattr("agent.services.execution_audit_service.log_audit", _fake_log)
    get_execution_audit_service().emit(
        operation_type="tool_intent_remap",
        outcome="remapped",
        trace_id="tr-1",
        goal_id="g-1",
        task_id="t-1",
        actor_role="hub",
        details={"token": "secret-value"},
    )
    assert captured["event_type"] == "execution_audit_event"
    assert captured["details"]["schema"] == "canonical_audit_event.v1"
    assert captured["details"]["operation_type"] == "tool_intent_remap"
    assert captured["details"]["role"] == "hub"
    assert "secret-value" not in str(captured["details"])


def test_execution_audit_write_operation_includes_required_mutation_details(monkeypatch) -> None:
    captured = {}

    def _fake_log(event_type, details):
        captured["event_type"] = event_type
        captured["details"] = details

    monkeypatch.setattr("agent.services.execution_audit_service.log_audit", _fake_log)
    get_execution_audit_service().emit_write_operation(
        trace_id="tr-write",
        task_id="t-write",
        goal_id="g-write",
        target_path_class="workspace_file",
        write_reason="apply_patch",
        approval_source="approval_queue",
        verification_result="verified",
        risk_level="high",
        actor_role="hub",
    )
    assert captured["event_type"] == "execution_audit_event"
    assert captured["details"]["operation_type"] == "write_operation"
    assert captured["details"]["target"]["target_path_class"] == "workspace_file"
    assert captured["details"]["details"]["approval_source"] == "approval_queue"
    assert captured["details"]["target"]["risk_level"] == "high"


def test_execution_audit_approval_and_transition_events(monkeypatch) -> None:
    captured: list[dict] = []

    def _fake_log(event_type, details):
        captured.append({"event_type": event_type, "details": details})

    monkeypatch.setattr("agent.services.execution_audit_service.log_audit", _fake_log)
    svc = get_execution_audit_service()
    svc.emit_approval_event(
        trace_id="tr-approval",
        task_id="t-approval",
        goal_id="g-approval",
        action="approve",
        approver_identity="alice",
        approval_scope="task_execution",
        approval_source="task_review_endpoint",
        write_allowed=True,
        actor_role="hub",
    )
    svc.emit_workflow_transition(
        trace_id="tr-flow",
        task_id="t-flow",
        goal_id="g-flow",
        from_state="in_progress",
        to_state="completed",
        trigger="execution_result_finalize",
        actor_role="hub",
    )
    assert captured[0]["details"]["operation_type"] == "approval_event"
    assert captured[0]["details"]["outcome"] == "approve"
    assert captured[1]["details"]["operation_type"] == "workflow_transition"
    assert captured[1]["details"]["target"]["from_state"] == "in_progress"
    assert captured[1]["details"]["target"]["to_state"] == "completed"
