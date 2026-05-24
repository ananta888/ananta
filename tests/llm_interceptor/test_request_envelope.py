from __future__ import annotations

from agent.services.llm_interceptor.request_envelope import build_request_envelope


def test_envelope_normal_chat_defaults():
    env = build_request_envelope(
        payload={"model": "x", "messages": [{"role": "user", "content": "hello"}]},
        headers={},
    )
    assert env.model == "x"
    assert env.stream is False
    assert env.caller_type == "unknown"


def test_envelope_preserves_ids_and_detects_opencode():
    env = build_request_envelope(
        payload={
            "model": "x",
            "messages": [{"role": "user", "content": "hello"}],
            "request_id": "req-1",
            "caller": {"source": "OpenCode"},
        },
        headers={"X-Correlation-ID": "corr-1"},
    )
    assert env.request_id == "req-1"
    assert env.correlation_id == "corr-1"
    assert env.caller_type == "opencode"


def test_envelope_streaming_and_tool_request():
    env = build_request_envelope(
        payload={
            "model": "x",
            "stream": True,
            "messages": [{"role": "user", "content": "tool please"}],
            "tools": [{"type": "function", "function": {"name": "run"}}],
        },
        headers={},
    )
    assert env.stream is True
    assert isinstance(env.raw_payload.get("tools"), list)

