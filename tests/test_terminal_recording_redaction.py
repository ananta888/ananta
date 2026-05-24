from __future__ import annotations

import pytest

from agent.services.terminal_recording_service import redact_secrets


def test_bearer_token_redacted():
    text = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc.def"
    result = redact_secrets(text)
    assert "eyJhbGciOiJSUzI1NiJ9" not in result
    assert "***REDACTED***" in result


def test_password_env_var_redacted():
    text = "ANANTA_PASSWORD=super_secret_value"
    result = redact_secrets(text)
    assert "super_secret_value" not in result
    assert "***REDACTED***" in result


def test_token_env_var_redacted():
    text = "ANANTA_TOKEN=mytoken123456"
    result = redact_secrets(text)
    assert "mytoken123456" not in result
    assert "***REDACTED***" in result


def test_api_key_redacted():
    text = "API_KEY=abcdefghij12345678"
    result = redact_secrets(text)
    assert "abcdefghij12345678" not in result
    assert "***REDACTED***" in result


def test_generic_password_redacted():
    text = "password=my-database-pw"
    result = redact_secrets(text)
    assert "my-database-pw" not in result
    assert "***REDACTED***" in result


def test_innocent_text_unchanged():
    text = "ls -la /home/user\ntotal 42\ndrwxr-xr-x 5 user user 4096 May 25"
    result = redact_secrets(text)
    assert result == text


def test_empty_string_unchanged():
    assert redact_secrets("") == ""


def test_raw_bearer_not_stored():
    text = "Bearer ghp_1234567890abcdef1234567890abcdef12"
    result = redact_secrets(text)
    assert "ghp_1234567890" not in result


def test_multiple_secrets_all_redacted():
    text = "token=abc123456789012345 and password=hunter2 and API_KEY=abcde12345678"
    result = redact_secrets(text)
    assert "abc123456789012345" not in result
    assert "hunter2" not in result
