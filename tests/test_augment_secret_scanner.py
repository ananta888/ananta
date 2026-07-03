from agent.services.augment.augment_secret_scanner import AugmentSecretScanner


def test_scan_clean_text():
    s = AugmentSecretScanner()
    result = s.scan_and_redact_text("hello world")
    assert result.clean is True
    assert result.redaction_count == 0


def test_redact_api_key():
    s = AugmentSecretScanner()
    result = s.scan_and_redact_text("api_key=sk-abc123defgh456789xyz")
    assert result.clean is False
    assert "[REDACTED]" in result.redacted_text


def test_redact_bearer_token():
    s = AugmentSecretScanner()
    result = s.scan_and_redact_text("bearer_token=eyJhbGciOiJIUzI1NiJ9.verylongtoken")
    assert result.redaction_count > 0


def test_is_secret_file_env():
    s = AugmentSecretScanner()
    assert s.is_secret_file(".env") is True
    assert s.is_secret_file("src/.env.local") is True


def test_is_secret_file_pem():
    s = AugmentSecretScanner()
    assert s.is_secret_file("certs/server.pem") is True


def test_is_not_secret_file():
    s = AugmentSecretScanner()
    assert s.is_secret_file("src/main.py") is False
    assert s.is_secret_file("tests/test_auth.py") is False


def test_filter_paths_blocks_secrets():
    s = AugmentSecretScanner()
    allowed, blocked = s.filter_paths(["src/main.py", ".env", "src/user.py"])
    assert ".env" in blocked
    assert "src/main.py" in allowed


def test_prepare_prompt_redacts_snippets():
    s = AugmentSecretScanner()
    prompt = "analyze this code"
    snippets = ["api_key=sk-secret123456789012345678", "normal code"]
    clean_p, clean_s, total = s.prepare_prompt_for_external(prompt, context_snippets=snippets)
    assert total > 0
    assert "[REDACTED]" in clean_s[0]


def test_aws_access_key_redacted():
    s = AugmentSecretScanner()
    result = s.scan_and_redact_text("key: AKIAIOSFODNN7EXAMPLE")
    assert result.redaction_count > 0
