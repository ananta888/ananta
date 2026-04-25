from __future__ import annotations

from scripts.e2e.e2e_artifacts import redact_sensitive_text, sanitize_report_payload


def test_redaction_hides_tokens_secrets_and_sensitive_paths() -> None:
    raw = (
        "token=supersecretvalue1234567890\n"
        "api_key: sk_live_12345678901234567890\n"
        "password = my-password\n"
        "path=/home/user/private/workspace\n"
        "windows=C:\\Users\\pst\\secrets\\vault.txt\n"
    )
    redacted = redact_sensitive_text(raw)

    assert "supersecretvalue1234567890" not in redacted
    assert "sk_live_12345678901234567890" not in redacted
    assert "my-password" not in redacted
    assert "/home/user/private/workspace" not in redacted
    assert "C:\\Users\\pst\\secrets\\vault.txt" not in redacted
    assert "<REDACTED>" in redacted or "<REDACTED_TOKEN>" in redacted
    assert "<REDACTED_PATH>" in redacted


def test_redaction_preserves_policy_and_audit_facts() -> None:
    raw = "status: denied\npolicy: unsafe_action_block\nexecuted: no\n"
    redacted = redact_sensitive_text(raw)
    assert "status: denied" in redacted
    assert "policy: unsafe_action_block" in redacted
    assert "executed: no" in redacted


def test_report_sanitization_redacts_nested_sensitive_values() -> None:
    payload = {
        "schema": "e2e_report.v1",
        "flows": [
            {
                "flow_id": "x",
                "notes": ["token=abc123abc123abc123abc123abc123", "path=/home/user/private/file.txt"],
                "artifact_refs": ["C:\\Users\\pst\\secret.txt"],
            }
        ],
    }
    sanitized = sanitize_report_payload(payload)
    notes = sanitized["flows"][0]["notes"]
    assert all("abc123abc123abc123abc123abc123" not in note for note in notes)
    assert all("/home/user/private/file.txt" not in note for note in notes)
    assert "<REDACTED_PATH>" in sanitized["flows"][0]["artifact_refs"][0]
