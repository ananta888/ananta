from __future__ import annotations

from agent.services.llm_interceptor.secret_redactor import SecretRedactor


def test_redacts_env_style_secret():
    out, meta = SecretRedactor().redact_text("API_KEY=abcd123456789")
    assert "[REDACTED]" in out
    assert meta["redaction_hits"] >= 1


def test_redacts_private_key_block():
    sample = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    out, _meta = SecretRedactor().redact_text(sample)
    assert "PRIVATE KEY" not in out or "[REDACTED]" in out


def test_redacts_messages_before_forward():
    msgs, meta = SecretRedactor().redact_messages(
        [{"role": "user", "content": "token: supersecret12345"}]
    )
    assert "[REDACTED]" in msgs[0]["content"]
    assert meta["redaction_hits"] >= 1

