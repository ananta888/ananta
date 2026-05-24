from __future__ import annotations

import json

from agent.services.llm_interceptor.audit_logger import AuditLogger


def test_audit_logger_records_required_fields_without_raw_prompt_default():
    event = AuditLogger(debug_prompt_logging=False).build_event(
        request_id="r1",
        caller_type="opencode",
        upstream_id="local",
        model="m1",
        policy_decision={"action": "allow"},
        redaction_meta={"redaction_hits": 2},
        duration_ms=12,
        messages=[{"role": "user", "content": "token=abc"}],
    )
    assert event["request_id"] == "r1"
    assert event["upstream_id"] == "local"
    assert "redacted_messages" not in event
    assert "token=abc" not in json.dumps(event)


def test_audit_logger_optional_redacted_messages_only():
    event = AuditLogger(debug_prompt_logging=True).build_event(
        request_id="r2",
        caller_type="opencode",
        upstream_id="cloud",
        model="m2",
        policy_decision={"action": "reduce_context"},
        redaction_meta={"redaction_hits": 1},
        duration_ms=5,
        messages=[{"role": "user", "content": "[REDACTED]"}],
    )
    assert "redacted_messages" in event
    assert event["redacted_messages"][0]["content"] == "[REDACTED]"

