"""SS05.02: Tests für View-Share Policy und Redaction."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.share_view_policy import (
    ViewSharePolicy,
    build_default_policy,
    build_policy_from_session_permissions,
    apply_redaction,
    check_snapshot,
    check_and_redact_snapshot,
)


def test_default_policy_view_share_disabled():
    policy = build_default_policy()
    assert not policy.view_share_enabled


def test_policy_from_permissions_view_tui_false():
    perms = {"view_tui": False, "chat": True}
    policy = build_policy_from_session_permissions(perms)
    assert not policy.view_share_enabled


def test_policy_from_permissions_view_tui_true():
    perms = {"view_tui": True, "chat": True}
    policy = build_policy_from_session_permissions(perms)
    assert policy.view_share_enabled


def test_check_snapshot_denied_when_disabled():
    policy = ViewSharePolicy(view_share_enabled=False)
    decision = check_snapshot("some content", policy)
    assert not decision.allowed
    assert decision.reason == "view_share_disabled"


def test_check_snapshot_allowed_when_enabled():
    policy = ViewSharePolicy(view_share_enabled=True)
    decision = check_snapshot("normal content", policy)
    assert decision.allowed


def test_redact_password_lines():
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=True)
    text = "user: admin\npassword: supersecret123\nother: fine"
    result = apply_redaction(text, policy)
    lines = result.splitlines()
    assert any("[REDACTED: sensitive]" in l for l in lines)
    assert any("user: admin" in l for l in lines)
    assert any("other: fine" in l for l in lines)


def test_redact_bearer_token():
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=True)
    text = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0"
    result = apply_redaction(text, policy)
    assert "[REDACTED" in result


def test_redact_notes_lines():
    policy = ViewSharePolicy(view_share_enabled=True, redact_notes=True)
    text = "public info\n[notes] private stuff\nmore public"
    result = apply_redaction(text, policy)
    lines = result.splitlines()
    assert any("[REDACTED: notes]" in l for l in lines)
    assert any("public info" in l for l in lines)


def test_no_redaction_when_disabled():
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=False, redact_notes=False)
    text = "password: secret\n[notes] private"
    result = apply_redaction(text, policy)
    assert result == text


def test_check_and_redact_snapshot():
    policy = ViewSharePolicy(view_share_enabled=True, redact_secrets=True)
    text = "hello\ntoken: abc123\nworld"
    decision, redacted = check_and_redact_snapshot(text, policy)
    assert decision.allowed
    assert "token: abc123" not in redacted
    assert "hello" in redacted
    assert "world" in redacted


def test_policy_hash_is_deterministic():
    policy = ViewSharePolicy(view_share_enabled=True)
    d1 = check_snapshot("same content", policy)
    d2 = check_snapshot("same content", policy)
    assert d1.policy_hash == d2.policy_hash


def test_policy_hash_changes_with_content():
    policy = ViewSharePolicy(view_share_enabled=True)
    d1 = check_snapshot("content A", policy)
    d2 = check_snapshot("content B", policy)
    assert d1.policy_hash != d2.policy_hash
