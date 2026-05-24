from __future__ import annotations

from agent.services.llm_interceptor.response_validator import ResponseValidator


def test_stream_chunk_valid():
    ok, reason = ResponseValidator().validate_stream_chunk("data: {\"id\":\"1\"}")
    assert ok is True
    assert reason == "ok"


def test_stream_chunk_done():
    ok, reason = ResponseValidator().validate_stream_chunk("data: [DONE]")
    assert ok is True
    assert reason == "done"


def test_stream_chunk_invalid():
    ok, reason = ResponseValidator().validate_stream_chunk("{\"id\":\"1\"}")
    assert ok is False
    assert reason == "missing_data_prefix"

