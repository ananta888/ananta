from __future__ import annotations

import json

from agent.services.llm_interceptor.audit_logger import AuditLogger


def test_audit_logger_records_required_fields_without_raw_prompt_default():
    event = AuditLogger(debug_prompt_logging=False).build_event(
        request_id="r1",
        correlation_id="corr-1",
        caller_type="opencode",
        upstream_id="local",
        model="m1",
        policy_decision={"action": "allow"},
        redaction_meta={"redaction_hits": 2},
        duration_ms=12,
        task_metadata={"task_id": "t-1", "prompt_bundle_class": "coding", "context_classes": ["repo", "artifact"]},
        messages=[{"role": "user", "content": "token=abc"}],
    )
    assert event["request_id"] == "r1"
    assert event["trace_id"] == "corr-1"
    assert event["upstream_id"] == "local"
    assert event["operation_type"] == "llm_interaction"
    assert event["prompt_bundle_class"] == "coding"
    assert event["context_classes"] == ["repo", "artifact"]
    assert "redacted_messages" not in event
    assert "token=abc" not in json.dumps(event)


def test_audit_logger_optional_redacted_messages_only():
    event = AuditLogger(debug_prompt_logging=True).build_event(
        request_id="r2",
        correlation_id=None,
        caller_type="opencode",
        upstream_id="cloud",
        model="m2",
        policy_decision={"action": "reduce_context"},
        redaction_meta={"redaction_hits": 1},
        duration_ms=5,
        task_metadata={"task_id": "t-2"},
        messages=[{"role": "user", "content": "[REDACTED]"}],
    )
    assert "redacted_messages" in event
    assert event["redacted_messages"][0]["content"] == "[REDACTED]"
    assert event["trace_id"] == "r2"
