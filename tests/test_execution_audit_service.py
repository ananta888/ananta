from __future__ import annotations

from agent.services.execution_audit_service import get_execution_audit_service


def test_execution_audit_schema_has_required_fields() -> None:
    schema = get_execution_audit_service().schema()
    assert schema["schema"] == "execution_audit_event.v1"
    assert "trace_id" in schema["required_fields"]
    assert "operation_type" in schema["required_fields"]


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
        details={"token": "secret-value"},
    )
    assert captured["event_type"] == "execution_audit_event"
    assert captured["details"]["schema"] == "execution_audit_event.v1"
    assert captured["details"]["operation_type"] == "tool_intent_remap"
    assert "secret-value" not in str(captured["details"])

