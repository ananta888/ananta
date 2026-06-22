"""
HCCA-006 — Tests for SecretRedactor and SensitivityLabel.

The module already exists; all tests should pass immediately.
"""
from __future__ import annotations

import pytest

from agent.services.context_compression.secret_redactor import (
    SecretRedactor,
    SensitivityLabel,
)


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor()


class TestSecretRedactorScan:
    def test_scan_safe_text_returns_safe(self, redactor):
        """Plain log text with no secrets → SensitivityLabel.SAFE."""
        text = "2026-06-22 10:00:00 INFO Worker started. Polling queue every 5 seconds."
        label = redactor.scan(text)
        assert label == SensitivityLabel.SAFE

    def test_scan_api_key_returns_secret(self, redactor):
        """Text containing an OpenAI-style sk- key → SECRET."""
        text = "config.api_key = 'sk-abcdefghij1234567890abcdefghij12'"
        label = redactor.scan(text)
        assert label == SensitivityLabel.SECRET

    def test_scan_bearer_token_returns_secret_or_sensitive(self, redactor):
        """Authorization Bearer header → at least SENSITIVE."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        label = redactor.scan(text)
        assert label in (SensitivityLabel.SECRET, SensitivityLabel.SENSITIVE)

    def test_scan_private_key_returns_secret(self, redactor):
        """PEM private key block → SECRET."""
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        label = redactor.scan(text)
        assert label == SensitivityLabel.SECRET

    def test_aws_key_pattern(self, redactor):
        """AWS access key ID pattern → SECRET."""
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        label = redactor.scan(text)
        assert label == SensitivityLabel.SECRET


class TestSecretRedactorRedact:
    def test_redact_replaces_matches(self, redactor):
        """Redacted text no longer contains the original secret value."""
        secret = "sk-abcdefghij1234567890abcdefghij12"
        text = f"Using api_key={secret} for auth"
        redacted_text, reasons = redactor.redact(text)
        assert secret not in redacted_text
        assert len(reasons) >= 1

    def test_redact_multiple_patterns(self, redactor):
        """Text with API key and Bearer token — both get redacted."""
        text = (
            "key=sk-abcdefghij1234567890abcdefghij12 "
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        )
        redacted_text, reasons = redactor.redact(text)
        assert "sk-abcdefghij1234567890abcdefghij12" not in redacted_text
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig" not in redacted_text
        assert len(reasons) >= 2

    def test_redact_preserves_non_secret_content(self, redactor):
        """Text surrounding a secret is preserved after redaction."""
        prefix = "The configuration uses key "
        suffix = " for the service endpoint."
        secret = "sk-abcdefghij1234567890abcdefghij12"
        text = f"{prefix}{secret}{suffix}"
        redacted_text, _ = redactor.redact(text)
        assert prefix in redacted_text
        assert suffix in redacted_text

    def test_redact_returns_tuple(self, redactor):
        """redact() always returns a (str, list) tuple."""
        result = redactor.redact("no secrets here")
        assert isinstance(result, tuple)
        assert len(result) == 2
        redacted_text, reasons = result
        assert isinstance(redacted_text, str)
        assert isinstance(reasons, list)

    def test_redact_safe_text_unchanged(self, redactor):
        """Safe text is returned unchanged with empty reasons list."""
        text = "2026-06-22 INFO all clear"
        redacted_text, reasons = redactor.redact(text)
        assert redacted_text == text
        assert reasons == []


class TestSecretRedactorIsSafe:
    def test_is_safe_to_store_false_for_secret(self, redactor):
        """Text containing a private key → is_safe_to_store returns False."""
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."
        assert redactor.is_safe_to_store(text) is False

    def test_is_safe_to_store_true_for_safe_text(self, redactor):
        """Plain log text → is_safe_to_store returns True."""
        text = "INFO polling queue, 3 tasks pending"
        assert redactor.is_safe_to_store(text) is True

    def test_is_safe_to_store_false_for_aws_key(self, redactor):
        """AWS access key → not safe to store."""
        text = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        assert redactor.is_safe_to_store(text) is False
