"""Unit tests for PromptRedactionService. PTI-027."""
from __future__ import annotations

import pytest


@pytest.fixture
def svc():
    from agent.services.prompt_redaction_service import PromptRedactionService
    return PromptRedactionService()


class TestPromptRedaction:
    def test_bearer_token_redacted(self, svc):
        result = svc.redact("Authorization: Bearer eyJtoken123")
        assert "eyJtoken123" not in result.redacted_text
        assert "[REDACTED:bearer_token]" in result.redacted_text
        assert result.secrets_detected > 0

    def test_openai_api_key_redacted(self, svc):
        result = svc.redact("OPENAI_API_KEY=sk-prod-abc123xyz")
        assert "sk-prod-abc123xyz" not in result.redacted_text
        assert result.secrets_detected > 0

    def test_anthropic_api_key_redacted(self, svc):
        result = svc.redact("Set ANTHROPIC_API_KEY=claude-key-xyz")
        assert "claude-key-xyz" not in result.redacted_text

    def test_hermes_api_key_redacted(self, svc):
        result = svc.redact("HERMES_API_KEY=hermes-secret-99")
        assert "hermes-secret-99" not in result.redacted_text

    def test_openrouter_api_key_redacted(self, svc):
        result = svc.redact("OPENROUTER_API_KEY=or-key-xyz123")
        assert "or-key-xyz123" not in result.redacted_text

    def test_generic_api_key_redacted(self, svc):
        result = svc.redact("api_key=abcdefgh1234")
        assert "abcdefgh1234" not in result.redacted_text

    def test_generic_token_redacted(self, svc):
        result = svc.redact("token=abcdefgh1234567")
        assert "abcdefgh1234567" not in result.redacted_text

    def test_password_field_redacted(self, svc):
        result = svc.redact("password=mysupersecret")
        assert "mysupersecret" not in result.redacted_text

    def test_private_key_block_redacted(self, svc):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\n-----END RSA PRIVATE KEY-----"
        result = svc.redact(pem)
        assert "MIIEowIBAAKCAQ" not in result.redacted_text
        assert "[REDACTED:private_key]" in result.redacted_text

    def test_sk_key_redacted(self, svc):
        result = svc.redact("Using key sk-abcdefghijklmnopqrstu")
        assert "sk-abcdefghijklmnopqrstu" not in result.redacted_text

    def test_normal_model_name_not_redacted(self, svc):
        result = svc.redact("Using model qwen2.5-coder:7b with ollama")
        assert "qwen2.5-coder:7b" in result.redacted_text
        assert result.secrets_detected == 0

    def test_empty_text_returns_empty(self, svc):
        result = svc.redact("")
        assert result.redacted_text == ""
        assert result.secrets_detected == 0

    def test_none_text_returns_empty(self, svc):
        result = svc.redact(None)
        assert result.redacted_text == ""

    def test_redaction_report_contains_pattern_ids(self, svc):
        result = svc.redact("OPENAI_API_KEY=key Authorization: Bearer tok123")
        assert len(result.pattern_ids) > 0

    def test_redact_dict_sanitizes_authorization_header(self, svc):
        d = {"Authorization": "Bearer secret-token", "model": "gemma"}
        out = svc.redact_dict(d)
        assert out["Authorization"] == "[REDACTED]"
        assert out["model"] == "gemma"
