from __future__ import annotations

from agent.services.llm_interceptor.response_validator import ResponseValidator


def test_valid_response_passes():
    ok, reason = ResponseValidator().validate_chat_completion(
        {
            "id": "x",
            "object": "chat.completion",
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }
    )
    assert ok is True
    assert reason == "ok"


def test_missing_choices_fails():
    ok, reason = ResponseValidator().validate_chat_completion({"id": "x", "object": "chat.completion", "model": "m"})
    assert ok is False
    assert "missing_fields" in reason or reason == "choices_invalid"


def test_invalid_tool_calls_fails():
    ok, reason = ResponseValidator().validate_chat_completion(
        {
            "id": "x",
            "object": "chat.completion",
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "ok", "tool_calls": "bad"}}],
        }
    )
    assert ok is False
    assert reason == "tool_calls_invalid"

