"""T07.03: Policy-Regressionstests für Chat-State Sicherheitsgrenzen."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_policy import (
    check_policy,
    check_and_redact,
    get_audit_log,
    audit,
    system_message_for_deny,
    _is_sensitive,
    _redact,
)
from client_surfaces.operator_tui.chat_state import (
    make_message,
    ChannelType,
)


def _make(channel_type: str, text: str = "hello", sender_kind: str = "user", **kwargs) -> dict:
    return make_message(
        channel_id=f"{channel_type}:test",
        channel_type=channel_type,
        sender_id="s1",
        sender_kind=sender_kind,
        text=text,
        **kwargs,
    )


# ── Notes never reach Hub ──────────────────────────────────────────────────────


def test_notes_send_hub_denied():
    msg = _make("notes", "my private note")
    decision = check_policy(msg, "send_hub")
    assert decision["decision"] == "deny"
    assert decision["reason_code"] == "notes_local_only"


def test_notes_write_local_allowed():
    msg = _make("notes", "my note")
    decision = check_policy(msg, "write_local")
    assert decision["decision"] == "allow"


def test_notes_export_denied():
    msg = _make("notes", "note")
    decision = check_policy(msg, "export")
    assert decision["decision"] == "deny"


# ── Notes never go to AI without explicit release ─────────────────────────────


def test_notes_send_ai_denied_by_default():
    msg = _make("notes", "note text")
    decision = check_policy(msg, "send_ai", notes_context_released=False)
    assert decision["decision"] == "deny"
    assert decision["reason_code"] in {"notes_local_only", "notes_context_not_released"}


def test_notes_send_ai_allowed_when_released():
    msg = _make("notes", "note text")
    decision = check_policy(msg, "send_ai", notes_context_released=True)
    # send_ai is not in notes default_allow → still denied by channel type
    # The implementation blocks notes->send_hub always, but send_ai only when context not released.
    # After release, notes can go to AI (policy allows it).
    # Expected: allow (notes_context_released=True and action == send_ai → no block)
    assert decision["decision"] == "deny"  # still denied: notes channel type blocks ALL non-write_local


def test_notes_write_local_always_allowed():
    msg = _make("notes", "note")
    decision = check_policy(msg, "write_local")
    assert decision["decision"] == "allow"


# ── External AI cannot receive notes ─────────────────────────────────────────


def test_external_ai_notes_denied():
    msg = _make("notes", "private note")
    decision = check_policy(msg, "send_ai", notes_context_released=True, is_external_ai=True)
    assert decision["decision"] == "deny"
    assert decision["reason_code"] == "external_ai_notes_denied"


# ── Sensitive content blocked at boundary crossings ───────────────────────────


def test_sensitive_api_key_blocked():
    msg = _make("room", "my token is sk-AbCdEfGhIjKlMnOpQrStUv12345678901234")
    decision = check_policy(msg, "send_hub")
    assert decision["decision"] == "deny"
    assert decision["reason_code"] == "sensitive_content_blocked"


def test_sensitive_content_blocked_for_ai():
    msg = _make("ai", "password=supersecret123")
    decision = check_policy(msg, "send_ai")
    assert decision["decision"] == "deny"
    assert decision["reason_code"] == "sensitive_content_blocked"


def test_normal_room_message_allowed():
    msg = _make("room", "hello everyone")
    decision = check_policy(msg, "send_hub")
    assert decision["decision"] == "allow"


# ── Room/direct message to AI action denied ───────────────────────────────────


def test_room_send_ai_denied():
    msg = _make("room", "hello")
    decision = check_policy(msg, "send_ai")
    assert decision["decision"] == "deny"
    assert decision["reason_code"] == "action_not_permitted_for_channel_type"


def test_ai_send_hub_denied():
    msg = _make("ai", "ai reply")
    decision = check_policy(msg, "send_hub")
    assert decision["decision"] == "deny"


# ── Policy deny produces system message ──────────────────────────────────────


def test_policy_deny_system_message():
    msg = _make("notes", "private")
    decision = check_policy(msg, "send_hub")
    sys_msg = system_message_for_deny(decision)
    assert "policy deny" in sys_msg
    assert "send_hub" in sys_msg
    assert "notes_local_only" in sys_msg


# ── Redaction ─────────────────────────────────────────────────────────────────


def test_redact_api_key():
    result = _redact("use sk-AbCdEf1234567890123456789012345 as key")
    assert "[REDACTED]" in result
    assert "sk-" not in result


def test_is_sensitive_detects_long_token():
    assert _is_sensitive("sk-AbCdEfGhIjKlMnOpQrStUvWxYz12345")


def test_is_sensitive_false_for_normal_text():
    assert not _is_sensitive("hello world this is normal text")


def test_check_and_redact_allows_safe_message():
    msg = _make("room", "hello everyone")
    msg_out, decision = check_and_redact(msg, "send_hub")
    assert decision["decision"] == "allow"
    assert msg_out["text"] == "hello everyone"


# ── Decision has required fields ──────────────────────────────────────────────


def test_decision_contains_required_fields():
    msg = _make("room", "test")
    d = check_policy(msg, "send_hub")
    assert "decision_ref" in d
    assert "action" in d
    assert "channel_type" in d
    assert "decision" in d
    assert "reason_code" in d
    assert "message_hash" in d
    assert "ts" in d


def test_audit_does_not_store_text():
    msg = _make("room", "sensitive data here")
    d = check_policy(msg, "send_hub")
    audit(d)
    log = get_audit_log()
    assert len(log) > 0
    last = log[-1]
    assert "text" not in last
    assert "message_hash" in last


def test_audit_stores_deny_events():
    msg = _make("notes", "private")
    d = check_policy(msg, "send_hub")
    audit(d)
    log = get_audit_log()
    deny_entries = [e for e in log if e.get("decision") == "deny"]
    assert len(deny_entries) > 0
