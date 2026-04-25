from __future__ import annotations

from worker.core.redaction import (
    enforce_redaction_gate,
    find_unredacted_secret_markers,
    redact_payload,
    redact_text,
    sanitize_subprocess_environment,
)


def test_redaction_handles_token_like_values() -> None:
    redacted = redact_text("token=sk-1234567890abcdefghijklmnop")
    assert "sk-1234567890abcdefghijklmnop" not in redacted
    assert "[REDACTED_TOKEN]" in redacted


def test_redaction_handles_path_like_values_without_false_positive_on_relative_path() -> None:
    redacted = redact_text("open /home/user/projects/ananta/file.py and keep src/main.py")
    assert "/home/user/projects/ananta/file.py" not in redacted
    assert "src/main.py" in redacted


def test_redaction_payload_and_environment_sanitization() -> None:
    payload = {"api_key": "sk-abcdef0123456789abcdef", "normal": "ok"}
    redacted_payload = redact_payload(payload)
    assert "[REDACTED_TOKEN]" in str(redacted_payload["api_key"])
    env = sanitize_subprocess_environment(
        {"OPENAI_API_KEY": "x", "PATH": "/usr/bin"},
        explicitly_allowed_sensitive_keys=set(),
    )
    assert "OPENAI_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"


def test_redaction_gate_blocks_unredacted_secret() -> None:
    ok, matches = enforce_redaction_gate("authorization=ghp_1234567890123456789012345678901234")
    assert ok is False
    assert find_unredacted_secret_markers("authorization=ghp_1234567890123456789012345678901234")
    assert matches
