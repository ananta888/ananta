from __future__ import annotations

from agent.services.helpcenter_log_extractor_service import extract_failure_log_insights


def test_log_extractor_detects_failure_patterns() -> None:
    text = """
=================== FAILURES ===================
E   AssertionError: expected 200
npm ERR! code ELIFECYCLE
"""
    result = extract_failure_log_insights(text, max_lines=50)
    assert "pytest_failure" in result["detected_patterns"]
    assert "npm_failure" in result["detected_patterns"]


def test_log_extractor_truncates_and_marks_notice() -> None:
    text = "\n".join(f"line {idx}" for idx in range(300))
    result = extract_failure_log_insights(text, max_lines=40)
    assert result["truncated"] is True
    assert result["line_count_excerpt"] == 40
    assert result["truncation_notice"] == "excerpt_truncated"


def test_log_extractor_redacts_secret_patterns() -> None:
    text = "token=supersecretvalue\nghp_1234567890123456789012345678901234"
    result = extract_failure_log_insights(text, max_lines=10)
    rendered = "\n".join(result["excerpt_lines"])
    assert "supersecretvalue" not in rendered
    assert "ghp_" not in rendered
    assert "[REDACTED_SECRET]" in rendered
