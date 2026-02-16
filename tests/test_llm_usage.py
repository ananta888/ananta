from unittest.mock import patch


def test_extract_llm_text_and_usage_from_strategy_result():
    from agent.llm_integration import extract_llm_text_and_usage

    result = {"text": "hello", "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}
    text, usage = extract_llm_text_and_usage(result)
    assert text == "hello"
    assert usage["prompt_tokens"] == 12
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 17


def test_call_llm_stores_usage_in_request_context(app):
    from flask import g
    from agent.llm_integration import _call_llm

    with app.test_request_context("/llm/generate", method="POST"):
        with patch("agent.llm_integration._execute_llm_call") as mock_exec:
            mock_exec.return_value = {
                "text": "ok",
                "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            }
            with patch("agent.llm_integration.settings") as mock_settings:
                mock_settings.retry_count = 0
                mock_settings.retry_backoff = 0.0
                out = _call_llm("openai", "m", "p", {"openai": "http://x"}, "k")

        assert out == "ok"
        assert g.llm_last_usage["prompt_tokens"] == 11
        assert g.llm_last_usage["completion_tokens"] == 4
        assert g.llm_last_usage["total_tokens"] == 15
